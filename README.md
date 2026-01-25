# Best Streamlit Website - Development Guide

A modern, full-featured Streamlit-based platform for team collaboration, AI agents, and DevOps tool integration using Model Context Protocol (MCP).

## Quick Start (Docker Compose)

The fastest way to get started is using Docker Compose:

### Prerequisites
- Docker Desktop (or Docker Engine + Docker Compose)
- 2-4GB RAM available
- Git

### Start the Full Stack

**Windows (PowerShell):**
```powershell
.\scripts\dev-start.ps1
```

**Linux/macOS:**
```bash
./scripts/dev-start.sh
```

This will start:
- Streamlit UI (http://localhost:8502)
- Scheduler MCP (http://localhost:8010)
- Docker MCP (http://localhost:8001)
- Jenkins MCP (http://localhost:8002)
- Kubernetes MCP (http://localhost:8003)

### Optional Services

**With AI (Ollama):**
```powershell
.\scripts\dev-start.ps1 -WithAI
# or
./scripts/dev-start.sh --ai
```

**With Development Tools (DB Admin):**
```powershell
.\scripts\dev-start.ps1 -WithTools
# or
./scripts/dev-start.sh --tools
```

**With Everything:**
```powershell
.\scripts\dev-start.ps1 -Full
# or
./scripts/dev-start.sh --full
```

### Other Useful Commands

```powershell
# View logs
.\scripts\dev-logs.ps1              # All services
.\scripts\dev-logs.ps1 streamlit    # Specific service
.\scripts\dev-logs.ps1 -Follow      # Follow log output

# Stop services
.\scripts\dev-stop.ps1              # Stop (keep containers)
.\scripts\dev-stop.ps1 -Remove      # Stop and remove

# Reset environment
.\scripts\dev-reset.ps1             # Full reset (WARNING: deletes data)
.\scripts\dev-reset.ps1 -KeepData   # Reset but keep databases
```

**Linux/macOS equivalents:** Replace `.ps1` with `.sh` and use `--` for flags (e.g., `--follow`, `--remove`).

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# Key settings for local development
OLLAMA_ENABLED=false          # Set to true to enable AI features
OLLAMA_MODEL=tinyllama        # Lightweight model for development
JENKINS_BASE_URL=http://jenkins:8080
K8S_KUBECONFIG=               # Leave empty to use ~/.kube/config
```

### Resource Requirements

**Minimal Configuration (Ollama disabled):**
- RAM: ~1.5GB
- CPU: 1.5 cores
- Disk: ~500MB

**Recommended Configuration (with Ollama):**
- RAM: ~2.5GB
- CPU: 2.2 cores
- Disk: ~1.5GB

**Full Configuration (all services + Ollama):**
- RAM: ~3.5GB
- CPU: 3 cores
- Disk: ~2GB

### Ollama Model Options

For local development on limited resources:

| Model | Size | RAM | Speed | Quality |
|-------|------|-----|-------|---------|
| **tinyllama** | 637MB | 800MB | Fast | Basic |
| **phi:mini** | 1.6GB | 2GB | Medium | Good |
| **qwen2.5:7b** | 4.8GB | 5GB | Slow | Excellent |

**To change models:**
```bash
# In .env file
OLLAMA_MODEL=tinyllama

# Or as environment variable
OLLAMA_MODEL=phi:mini docker-compose up
```

**Pull a model (if using Ollama):**
```bash
docker-compose exec ollama ollama pull tinyllama
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Streamlit Web UI (8502)                  │
│  Team Tasks • WFH Schedule • Agents • K8s • Jenkins         │
└──────────────────┬──────────────────────────────────────────┘
                   │
    ┌──────────────┴──────────────┐
    │                             │
┌───▼────────────────┐  ┌─────────▼──────────────┐
│   MCP Servers      │  │   SQLite Databases     │
│ (FastMCP Framework)│  │  - tasks.db            │
│                    │  │  - scheduler.db        │
│ • Scheduler (8010) │  └────────────────────────┘
│ • Jenkins (8002)   │
│ • Kubernetes (8003)│
│ • Docker (8001)    │
└────────────────────┘
```

### Key Components

1. **Streamlit UI** - Multi-page web application
   - Team task management (Kanban board)
   - WFH scheduling
   - AI agents (DataGen, DevOps)
   - Kubernetes cluster management
   - Jenkins pipeline integration

2. **MCP Servers** - Tool integration layer
   - Scheduler: Background job orchestration
   - Jenkins: CI/CD operations
   - Kubernetes: Cluster management
   - Docker: Container operations

3. **Data Layer** - SQLite for local dev
   - Task tracking and history
   - Scheduler job definitions
   - User schedules and preferences

---

## Features

### Team Collaboration
- **Task Manager**: Kanban board with Backlog → In Progress → Review → Done
- **WFH Scheduling**: 2-week rotation scheduling with team calendar
- **Analytics**: Task velocity, cycle time, team metrics

### AI Agents
- **DataGen Agent**: Generate synthetic user data (with/without Ollama)
- **DevOps Referral Agent**: Parse resumes and generate hiring signals
- **Mock Data Fallback**: Works without Ollama using built-in generators

### DevOps Integration
- **Jenkins**: Trigger builds, view logs, monitor pipelines
- **Kubernetes**: Manage clusters, deploy Helm charts, view resources
- **Docker**: Inspect containers, manage images
- **Scheduler**: Automated health checks and recurring jobs

---

## Development

### Hot Reload

All services support hot-reload in development mode:

- **Streamlit**: Auto-reloads on file changes (requires `watchdog`)
- **MCP Servers**: Source code mounted as volumes
- **Scheduler**: Restarts automatically when code changes

### Database Access

SQLite databases are stored in `./data/`:

```bash
# Access via DB Admin tool (if started with -WithTools)
http://localhost:8090

# Or use SQLite CLI
sqlite3 data/tasks.db
sqlite3 data/scheduler.db
```

### Debugging

**View logs:**
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f streamlit
docker-compose logs -f scheduler
```

**Access container shell:**
```bash
docker-compose exec streamlit sh
docker-compose exec scheduler sh
```

**Health checks:**
```bash
# Check all services
docker-compose ps

# Test endpoints
curl http://localhost:8502/_stcore/health  # Streamlit
curl http://localhost:8010/health          # Scheduler
curl http://localhost:8001/health          # Docker MCP
```

---

## Production Deployment

### Kubernetes (Helm)

Deploy to K8s cluster:

```bash
# Using values-staging.yaml profile
helm install best-streamlit ./best-streamlit-website/deploy/helm/best-streamlit-website \
  -f ./best-streamlit-website/deploy/helm/best-streamlit-website/values-staging.yaml \
  -n best-streamlit-website --create-namespace

# Check deployment
kubectl get pods -n best-streamlit-website
kubectl get svc -n best-streamlit-website
```

### Docker Compose (Production)

For production deployment without K8s:

```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

This configuration includes:
- PostgreSQL (instead of SQLite)
- Redis (for caching and sessions)
- Increased resource limits
- Production-grade Ollama model

---

## Troubleshooting

### Services Won't Start

**Check Docker is running:**
```bash
docker info
```

**Check ports are available:**
```bash
# Windows
netstat -ano | findstr :8502
netstat -ano | findstr :8010

# Linux/macOS
lsof -i :8502
lsof -i :8010
```

**Reset everything:**
```bash
.\scripts\dev-reset.ps1
.\scripts\dev-start.ps1
```

### Ollama Connection Failed

If you see "Could not connect to Ollama":

1. **Check Ollama is running:**
   ```bash
   curl http://localhost:11434/api/tags
   ```

2. **Disable Ollama to use mock data:**
   ```bash
   # In .env file
   OLLAMA_ENABLED=false
   ```

3. **Pull the model:**
   ```bash
   docker-compose exec ollama ollama pull tinyllama
   ```

### Database Locked Errors

SQLite doesn't support multiple writers. For concurrent access:

1. **Use PostgreSQL (production mode):**
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.prod.yml up
   ```

2. **Or reset the database:**
   ```bash
   .\scripts\dev-reset.ps1 -KeepData:$false
   ```

### Permission Denied (Linux/macOS)

Make scripts executable:
```bash
chmod +x scripts/*.sh
```

### Out of Memory

Reduce memory usage:

1. **Disable Ollama:**
   ```bash
   OLLAMA_ENABLED=false docker-compose up
   ```

2. **Start minimal services:**
   ```bash
   docker-compose up streamlit scheduler docker-mcp
   ```

3. **Increase Docker memory limit:**
   - Docker Desktop → Settings → Resources → Memory → 4GB+

---

## Project Structure

```
.
├── docker-compose.yml           # Base compose configuration
├── docker-compose.dev.yml       # Development overrides
├── docker-compose.prod.yml      # Production overrides
├── .env.example                 # Environment template
├── scripts/                     # Development scripts
│   ├── dev-start.ps1|sh        # Start stack
│   ├── dev-stop.ps1|sh         # Stop stack
│   ├── dev-logs.ps1|sh         # View logs
│   └── dev-reset.ps1|sh        # Reset environment
├── best-streamlit-website/      # Main application
│   ├── app.py                   # Entry point
│   ├── pages/                   # Streamlit pages
│   │   ├── 0_Home.py
│   │   ├── 1_Team_Task_Manager.py
│   │   ├── 4_DataGen_Agent.py
│   │   └── ...
│   ├── src/                     # Source code
│   │   ├── ai/                  # AI agents and MCP servers
│   │   ├── scheduler/           # Background scheduler
│   │   └── *.py                 # Utilities
│   └── deploy/                  # Docker and K8s configs
│       ├── streamlit/
│       ├── scheduler/
│       ├── mcp-jenkins/
│       ├── mcp-kubernetes/
│       ├── mcp-docker/
│       ├── helm/                # Helm charts
│       └── k8s/                 # Kubernetes manifests
└── data/                        # Persistent data (local dev)
    ├── tasks.db
    ├── scheduler.db
    └── ...
```

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Test locally with Docker Compose
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

### Development Guidelines

- Use Docker Compose for local development
- Test with Ollama disabled (mock data mode)
- Ensure hot-reload works for your changes
- Add tests where applicable
- Update documentation

---

## License

MIT License - See LICENSE file for details

---

## Support

- **Issues**: https://github.com/yourusername/best-streamlit-website/issues
- **Documentation**: See `/docs` directory
- **MCP Specification**: https://modelcontextprotocol.io

---

## Changelog

### 2026-01-24 - v0.2.0
- Added Docker Compose for local development
- Implemented Ollama graceful degradation with mock data
- Created development scripts (start, stop, logs, reset)
- Updated configuration for resource-constrained environments
- Changed default Ollama model to `tinyllama` for better performance

### Earlier Versions
- See `best-streamlit-website/README.md` for original documentation
