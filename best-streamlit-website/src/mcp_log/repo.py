"""MCP Log repository functions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, desc, and_, text, Integer
from sqlalchemy.exc import SQLAlchemyError

from .config import get_config
from .db import get_engine, get_session
from .models import Base, MCPToolCall, MCPServerHealth


def init_db(database_url: Optional[str] = None) -> None:
    """Initialize the database tables.

    Creates all tables if they don't exist. Safe to call multiple times.

    Args:
        database_url: Optional database URL override.
    """
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)


def _hash_args(args: Dict[str, Any]) -> str:
    """Create a hash of the arguments for deduplication."""
    # Sort keys for consistent hashing
    serialized = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def _redact_sensitive(args: Dict[str, Any]) -> Dict[str, Any]:
    """Redact sensitive information from arguments."""
    sensitive_keys = {
        "password", "token", "api_token", "secret", "key", "credential",
        "_client_token", "auth", "authorization", "api_key", "apikey",
    }

    redacted = {}
    for k, v in args.items():
        key_lower = k.lower()
        if any(sens in key_lower for sens in sensitive_keys):
            redacted[k] = "***REDACTED***"
        elif isinstance(v, dict):
            redacted[k] = _redact_sensitive(v)
        else:
            redacted[k] = v

    return redacted


def log_tool_call(
    server_name: str,
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    success: bool = False,
    result_preview: Optional[str] = None,
    error_message: Optional[str] = None,
    error_type: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    duration_ms: Optional[float] = None,
    source: Optional[str] = None,
    request_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    database_url: Optional[str] = None,
) -> Optional[str]:
    """Log an MCP tool call to the database.

    Args:
        server_name: Name of the MCP server
        tool_name: Name of the tool called
        args: Tool arguments (sensitive data will be redacted)
        success: Whether the call succeeded
        result_preview: Truncated result for debugging
        error_message: Error message if failed
        error_type: Type of error if failed
        started_at: When the call started
        finished_at: When the call finished
        duration_ms: Duration in milliseconds
        source: Source of the call (e.g., page name)
        request_id: Request correlation ID
        session_id: Session ID
        user_id: User ID
        database_url: Optional database URL override

    Returns:
        The ID of the created log entry, or None if logging failed.
    """
    config = get_config()
    if not config.enabled:
        return None

    try:
        session = get_session(database_url)

        # Redact and serialize args
        args_json = None
        args_hash = None
        if args:
            redacted_args = _redact_sensitive(args)
            args_json = json.dumps(redacted_args, default=str)[:10000]  # Limit size
            args_hash = _hash_args(args)

        # Truncate result preview
        if result_preview and len(result_preview) > 5000:
            result_preview = result_preview[:5000] + "...[truncated]"

        log_entry = MCPToolCall(
            server_name=server_name,
            tool_name=tool_name,
            args_json=args_json,
            args_hash=args_hash,
            success=success,
            result_preview=result_preview,
            error_message=error_message[:2000] if error_message else None,
            error_type=error_type,
            started_at=started_at or datetime.utcnow(),
            finished_at=finished_at,
            duration_ms=duration_ms,
            source=source,
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
        )

        session.add(log_entry)
        session.commit()
        log_id = log_entry.id
        session.close()

        return log_id

    except SQLAlchemyError:
        # Logging should never break the main application
        return None


def get_tool_calls(
    server_name: Optional[str] = None,
    tool_name: Optional[str] = None,
    success: Optional[bool] = None,
    source: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
    database_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query tool call logs with filtering.

    Args:
        server_name: Filter by server name
        tool_name: Filter by tool name
        success: Filter by success status
        source: Filter by source
        since: Filter by start time (inclusive)
        until: Filter by start time (exclusive)
        limit: Maximum number of results
        offset: Number of results to skip
        database_url: Optional database URL override

    Returns:
        List of tool call dictionaries.
    """
    try:
        session = get_session(database_url)

        query = session.query(MCPToolCall)

        if server_name:
            query = query.filter(MCPToolCall.server_name == server_name)
        if tool_name:
            query = query.filter(MCPToolCall.tool_name == tool_name)
        if success is not None:
            query = query.filter(MCPToolCall.success == success)
        if source:
            query = query.filter(MCPToolCall.source == source)
        if since:
            query = query.filter(MCPToolCall.started_at >= since)
        if until:
            query = query.filter(MCPToolCall.started_at < until)

        query = query.order_by(desc(MCPToolCall.started_at))
        query = query.limit(limit).offset(offset)

        results = [row.to_dict() for row in query.all()]
        session.close()

        return results

    except SQLAlchemyError:
        return []


def get_tool_call(log_id: str, database_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a single tool call log by ID.

    Args:
        log_id: The log entry ID
        database_url: Optional database URL override

    Returns:
        Tool call dictionary or None if not found.
    """
    try:
        session = get_session(database_url)
        row = session.query(MCPToolCall).filter(MCPToolCall.id == log_id).first()
        result = row.to_dict() if row else None
        session.close()
        return result
    except SQLAlchemyError:
        return None


def get_tool_call_stats(
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    database_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Get aggregate statistics for tool calls.

    Args:
        since: Start of time range
        until: End of time range
        database_url: Optional database URL override

    Returns:
        Dictionary with statistics.
    """
    if since is None:
        since = datetime.utcnow() - timedelta(days=7)
    if until is None:
        until = datetime.utcnow()

    try:
        session = get_session(database_url)

        # Base filter
        base_filter = and_(
            MCPToolCall.started_at >= since,
            MCPToolCall.started_at < until,
        )

        # Total calls
        total_calls = session.query(func.count(MCPToolCall.id)).filter(base_filter).scalar() or 0

        # Successful calls
        successful_calls = session.query(func.count(MCPToolCall.id)).filter(
            and_(base_filter, MCPToolCall.success == True)
        ).scalar() or 0

        # Failed calls
        failed_calls = total_calls - successful_calls

        # Average duration
        avg_duration = session.query(func.avg(MCPToolCall.duration_ms)).filter(
            and_(base_filter, MCPToolCall.duration_ms.isnot(None))
        ).scalar()

        # Max duration
        max_duration = session.query(func.max(MCPToolCall.duration_ms)).filter(
            and_(base_filter, MCPToolCall.duration_ms.isnot(None))
        ).scalar()

        # Unique servers
        unique_servers = session.query(func.count(func.distinct(MCPToolCall.server_name))).filter(
            base_filter
        ).scalar() or 0

        # Unique tools
        unique_tools = session.query(func.count(func.distinct(MCPToolCall.tool_name))).filter(
            base_filter
        ).scalar() or 0

        session.close()

        success_rate = (successful_calls / total_calls * 100) if total_calls > 0 else 0

        return {
            "total_calls": total_calls,
            "successful_calls": successful_calls,
            "failed_calls": failed_calls,
            "success_rate": round(success_rate, 2),
            "avg_duration_ms": round(avg_duration, 2) if avg_duration else None,
            "max_duration_ms": round(max_duration, 2) if max_duration else None,
            "unique_servers": unique_servers,
            "unique_tools": unique_tools,
            "since": since.isoformat(),
            "until": until.isoformat(),
        }

    except SQLAlchemyError:
        return {
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "success_rate": 0,
            "avg_duration_ms": None,
            "max_duration_ms": None,
            "unique_servers": 0,
            "unique_tools": 0,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        }


def get_server_stats(
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    database_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get statistics grouped by server.

    Args:
        since: Start of time range
        until: End of time range
        database_url: Optional database URL override

    Returns:
        List of server statistics.
    """
    if since is None:
        since = datetime.utcnow() - timedelta(days=7)
    if until is None:
        until = datetime.utcnow()

    try:
        session = get_session(database_url)

        base_filter = and_(
            MCPToolCall.started_at >= since,
            MCPToolCall.started_at < until,
        )

        # Query with grouping
        results = session.query(
            MCPToolCall.server_name,
            func.count(MCPToolCall.id).label("total_calls"),
            func.sum(func.cast(MCPToolCall.success, Integer)).label("successful_calls"),
            func.avg(MCPToolCall.duration_ms).label("avg_duration_ms"),
            func.max(MCPToolCall.duration_ms).label("max_duration_ms"),
            func.count(func.distinct(MCPToolCall.tool_name)).label("unique_tools"),
        ).filter(base_filter).group_by(MCPToolCall.server_name).all()

        stats = []
        for row in results:
            total = row.total_calls or 0
            successful = row.successful_calls or 0
            success_rate = (successful / total * 100) if total > 0 else 0

            stats.append({
                "server_name": row.server_name,
                "total_calls": total,
                "successful_calls": int(successful),
                "failed_calls": total - int(successful),
                "success_rate": round(success_rate, 2),
                "avg_duration_ms": round(row.avg_duration_ms, 2) if row.avg_duration_ms else None,
                "max_duration_ms": round(row.max_duration_ms, 2) if row.max_duration_ms else None,
                "unique_tools": row.unique_tools or 0,
            })

        session.close()
        return sorted(stats, key=lambda x: x["total_calls"], reverse=True)

    except SQLAlchemyError:
        return []


def get_tool_stats(
    server_name: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 20,
    database_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get statistics grouped by tool.

    Args:
        server_name: Filter by server
        since: Start of time range
        until: End of time range
        limit: Maximum number of tools to return
        database_url: Optional database URL override

    Returns:
        List of tool statistics.
    """
    if since is None:
        since = datetime.utcnow() - timedelta(days=7)
    if until is None:
        until = datetime.utcnow()

    try:
        session = get_session(database_url)

        filters = [
            MCPToolCall.started_at >= since,
            MCPToolCall.started_at < until,
        ]
        if server_name:
            filters.append(MCPToolCall.server_name == server_name)

        results = session.query(
            MCPToolCall.server_name,
            MCPToolCall.tool_name,
            func.count(MCPToolCall.id).label("total_calls"),
            func.sum(func.cast(MCPToolCall.success, Integer)).label("successful_calls"),
            func.avg(MCPToolCall.duration_ms).label("avg_duration_ms"),
        ).filter(and_(*filters)).group_by(
            MCPToolCall.server_name, MCPToolCall.tool_name
        ).order_by(desc("total_calls")).limit(limit).all()

        stats = []
        for row in results:
            total = row.total_calls or 0
            successful = row.successful_calls or 0
            success_rate = (successful / total * 100) if total > 0 else 0

            stats.append({
                "server_name": row.server_name,
                "tool_name": row.tool_name,
                "total_calls": total,
                "successful_calls": int(successful),
                "success_rate": round(success_rate, 2),
                "avg_duration_ms": round(row.avg_duration_ms, 2) if row.avg_duration_ms else None,
            })

        session.close()
        return stats

    except SQLAlchemyError:
        return []


def get_hourly_stats(
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    database_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get call counts grouped by hour.

    Args:
        since: Start of time range
        until: End of time range
        database_url: Optional database URL override

    Returns:
        List of hourly statistics.
    """
    if since is None:
        since = datetime.utcnow() - timedelta(hours=24)
    if until is None:
        until = datetime.utcnow()

    try:
        session = get_session(database_url)

        # Use strftime for SQLite compatibility
        backend = get_engine(database_url).url.get_backend_name()

        if backend == "sqlite":
            hour_expr = func.strftime("%Y-%m-%d %H:00:00", MCPToolCall.started_at)
        else:
            # PostgreSQL
            hour_expr = func.date_trunc("hour", MCPToolCall.started_at)

        base_filter = and_(
            MCPToolCall.started_at >= since,
            MCPToolCall.started_at < until,
        )

        results = session.query(
            hour_expr.label("hour"),
            func.count(MCPToolCall.id).label("total_calls"),
            func.sum(func.cast(MCPToolCall.success, Integer)).label("successful_calls"),
        ).filter(base_filter).group_by(hour_expr).order_by(hour_expr).all()

        stats = []
        for row in results:
            hour_str = str(row.hour) if row.hour else None
            total = row.total_calls or 0
            successful = row.successful_calls or 0

            stats.append({
                "hour": hour_str,
                "total_calls": total,
                "successful_calls": int(successful),
                "failed_calls": total - int(successful),
            })

        session.close()
        return stats

    except SQLAlchemyError:
        return []


def get_recent_errors(
    limit: int = 20,
    since: Optional[datetime] = None,
    database_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get recent failed tool calls.

    Args:
        limit: Maximum number of errors to return
        since: Start of time range
        database_url: Optional database URL override

    Returns:
        List of recent errors.
    """
    if since is None:
        since = datetime.utcnow() - timedelta(days=1)

    try:
        session = get_session(database_url)

        query = session.query(MCPToolCall).filter(
            and_(
                MCPToolCall.started_at >= since,
                MCPToolCall.success == False,
            )
        ).order_by(desc(MCPToolCall.started_at)).limit(limit)

        results = [row.to_dict() for row in query.all()]
        session.close()
        return results

    except SQLAlchemyError:
        return []


def cleanup_old_logs(
    retention_days: Optional[int] = None,
    database_url: Optional[str] = None,
) -> int:
    """Delete logs older than retention period.

    Args:
        retention_days: Number of days to keep (defaults to config)
        database_url: Optional database URL override

    Returns:
        Number of deleted records.
    """
    config = get_config()
    if retention_days is None:
        retention_days = config.retention_days

    cutoff = datetime.utcnow() - timedelta(days=retention_days)

    try:
        session = get_session(database_url)

        deleted = session.query(MCPToolCall).filter(
            MCPToolCall.created_at < cutoff
        ).delete(synchronize_session=False)

        session.commit()
        session.close()
        return deleted

    except SQLAlchemyError:
        return 0


def log_server_health(
    server_name: str,
    healthy: bool,
    response_time_ms: Optional[float] = None,
    error_message: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    database_url: Optional[str] = None,
) -> Optional[str]:
    """Log a server health check result.

    Args:
        server_name: Name of the MCP server
        healthy: Whether the server is healthy
        response_time_ms: Response time in milliseconds
        error_message: Error message if unhealthy
        details: Additional health check details
        database_url: Optional database URL override

    Returns:
        The ID of the created log entry, or None if logging failed.
    """
    config = get_config()
    if not config.enabled:
        return None

    try:
        session = get_session(database_url)

        details_json = json.dumps(details, default=str) if details else None

        log_entry = MCPServerHealth(
            server_name=server_name,
            healthy=healthy,
            response_time_ms=response_time_ms,
            error_message=error_message[:2000] if error_message else None,
            details_json=details_json,
        )

        session.add(log_entry)
        session.commit()
        log_id = log_entry.id
        session.close()

        return log_id

    except SQLAlchemyError:
        return None


def get_server_health_history(
    server_name: str,
    since: Optional[datetime] = None,
    limit: int = 100,
    database_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get health check history for a server.

    Args:
        server_name: Name of the MCP server
        since: Start of time range
        limit: Maximum number of results
        database_url: Optional database URL override

    Returns:
        List of health check results.
    """
    if since is None:
        since = datetime.utcnow() - timedelta(hours=24)

    try:
        session = get_session(database_url)

        query = session.query(MCPServerHealth).filter(
            and_(
                MCPServerHealth.server_name == server_name,
                MCPServerHealth.checked_at >= since,
            )
        ).order_by(desc(MCPServerHealth.checked_at)).limit(limit)

        results = [row.to_dict() for row in query.all()]
        session.close()
        return results

    except SQLAlchemyError:
        return []
