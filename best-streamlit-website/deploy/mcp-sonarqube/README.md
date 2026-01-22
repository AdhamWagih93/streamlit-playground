# SonarQube MCP Server Deployment

This directory contains Docker deployment configuration for the SonarQube MCP Server.

## Quick Start

### 1. Configure Environment

Copy the example environment file and update with your SonarQube credentials:

```bash
cp .env.example .env
# Edit .env with your SonarQube server URL and token
```

### 2. Build and Run

```bash
# Build the Docker image
docker-compose build

# Start the MCP server (and optionally SonarQube server)
docker-compose up -d

# View logs
docker-compose logs -f sonarqube-mcp
```

### 3. Test Connectivity

```bash
# Health check
curl http://localhost:8002/health

# Test a tool call (if HTTP transport is enabled)
curl -X POST http://localhost:8002/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "sonarqube_ping_server",
    "arguments": {
      "_client_token": "dev-sonarqube-mcp-token"
    }
  }'
```

## Configuration

### Required Environment Variables

```bash
SONARQUBE_BASE_URL=http://sonarqube:9000
SONARQUBE_TOKEN=your_sonarqube_token_here
```

### Optional Configuration

```bash
# Alternative authentication (less secure)
SONARQUBE_USERNAME=admin
SONARQUBE_PASSWORD=admin

# SSL settings
SONARQUBE_VERIFY_SSL=true

# MCP server settings
SONARQUBE_MCP_CLIENT_TOKEN=your-secure-token
SONARQUBE_MCP_TRANSPORT=http
SONARQUBE_MCP_HOST=0.0.0.0
SONARQUBE_MCP_PORT=8002
```

## Services

### sonarqube-mcp

The MCP server that exposes SonarQube API as MCP tools.

- **Port**: 8002
- **Health**: http://localhost:8002/health
- **Dependencies**: Requires access to SonarQube server

### sonarqube (Optional)

SonarQube Community Edition server for local development.

- **Port**: 9000
- **Web UI**: http://localhost:9000
- **Default Credentials**: admin/admin (change on first login)
- **Data**: Persisted in Docker volumes

## Usage with AI Assistants

### Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sonarqube": {
      "command": "docker",
      "args": ["exec", "-i", "sonarqube-mcp-server", "python", "-m", "src.ai.mcp_servers.sonarqube.mcp"],
      "env": {
        "SONARQUBE_MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

### Kubernetes Deployment

See `../k8s/mcp-sonarqube.yaml` for Kubernetes deployment manifests.

## Development

### Building Locally

```bash
# From repository root
docker build -t sonarqube-mcp:latest -f deploy/mcp-sonarqube/Dockerfile .
```

### Running Without Docker

```bash
# From repository root
export SONARQUBE_BASE_URL=http://localhost:9000
export SONARQUBE_TOKEN=your_token
python -m src.ai.mcp_servers.sonarqube.mcp
```

## Volumes

The docker-compose configuration creates persistent volumes for SonarQube:

- `sonarqube_data`: Analysis data and database
- `sonarqube_logs`: Server logs
- `sonarqube_extensions`: Plugins and extensions

## Network

Both services run on the `sonarqube-network` bridge network, allowing:
- MCP server to communicate with SonarQube server
- External access via published ports

## Troubleshooting

### MCP Server Won't Start

1. Check environment variables are set correctly
2. Verify SonarQube server is accessible
3. Check logs: `docker-compose logs sonarqube-mcp`

### Authentication Errors

1. Verify token is valid: Log into SonarQube → My Account → Security
2. Ensure token has necessary permissions
3. For user/password auth, check credentials are correct

### SonarQube Server Issues

1. Wait for initialization (can take 2-3 minutes on first start)
2. Check memory limits: SonarQube needs at least 2GB RAM
3. View logs: `docker-compose logs sonarqube`

### Connection Refused

1. Ensure services are running: `docker-compose ps`
2. Check ports aren't already in use
3. Verify network connectivity: `docker network inspect sonarqube-network`

## Cleanup

```bash
# Stop services
docker-compose down

# Remove volumes (WARNING: Deletes all SonarQube data)
docker-compose down -v

# Remove images
docker rmi sonarqube-mcp:latest
```

## Security Notes

1. **Change default credentials** for SonarQube (admin/admin)
2. **Use tokens instead of passwords** for authentication
3. **Secure the MCP client token** in production
4. **Enable SSL/TLS** for production deployments
5. **Restrict network access** using firewall rules
6. **Regular token rotation** for service accounts

## Next Steps

- Configure SonarQube quality gates and rules
- Set up CI/CD integration
- Configure project analysis
- Set up webhooks for notifications
- Integrate with your AI assistant

For more details, see the main README in `src/ai/mcp_servers/sonarqube/`.
