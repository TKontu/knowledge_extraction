#!/bin/bash
# Database migration helper script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Helper functions
info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if virtual environment is activated
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -f ".venv/bin/activate" ]; then
        info "Activating virtual environment..."
        source .venv/bin/activate
    else
        warn "No virtual environment found. Using system Python."
    fi
fi

# Check if DATABASE_URL is set
if [ -z "$DATABASE_URL" ]; then
    warn "DATABASE_URL not set. Using default: postgresql://techfacts:techfacts@localhost:5432/techfacts"
    export DATABASE_URL="postgresql://techfacts:techfacts@localhost:5432/techfacts"
fi

# Main command handler
case "${1}" in
    upgrade)
        info "Applying migrations..."
        python -m alembic upgrade head
        info "Migrations applied successfully!"
        ;;

    downgrade)
        if [ -z "$2" ]; then
            warn "Rolling back 1 migration..."
            python -m alembic downgrade -1
        else
            warn "Rolling back to version: $2"
            python -m alembic downgrade "$2"
        fi
        ;;

    current)
        info "Current migration version:"
        python -m alembic current
        ;;

    history)
        info "Migration history:"
        python -m alembic history --verbose
        ;;

    create)
        if [ -z "$2" ]; then
            error "Usage: $0 create <migration_name>"
            exit 1
        fi
        info "Creating new migration: $2"
        python -m alembic revision --autogenerate -m "$2"
        ;;

    create-manual)
        if [ -z "$2" ]; then
            error "Usage: $0 create-manual <migration_name>"
            exit 1
        fi
        info "Creating empty migration template: $2"
        python -m alembic revision -m "$2"
        ;;

    test)
        info "Testing migration upgrade/downgrade cycle..."

        info "Getting current version..."
        CURRENT=$(python -m alembic current | grep -oP '(?<=\(head\)|(?<=Rev: ))[a-f0-9]+' || echo "none")

        info "Applying all migrations..."
        python -m alembic upgrade head

        info "Rolling back one migration..."
        python -m alembic downgrade -1

        info "Re-applying migration..."
        python -m alembic upgrade head

        info "✓ Migration test completed successfully!"
        ;;

    fresh)
        warn "This will drop ALL tables and reapply migrations!"
        read -p "Are you sure? (yes/no): " -r
        if [[ $REPLY == "yes" ]]; then
            info "Dropping all migrations..."
            python -m alembic downgrade base

            info "Reapplying all migrations..."
            python -m alembic upgrade head

            info "✓ Fresh migration completed!"
        else
            info "Cancelled."
        fi
        ;;

    help|*)
        echo "Database Migration Helper"
        echo ""
        echo "Usage: $0 <command> [args]"
        echo ""
        echo "Commands:"
        echo "  upgrade              Apply all pending migrations"
        echo "  downgrade [version]  Rollback migrations (default: -1)"
        echo "  current              Show current migration version"
        echo "  history              Show migration history"
        echo "  create <name>        Create new migration (autogenerate)"
        echo "  create-manual <name> Create empty migration template"
        echo "  test                 Test upgrade/downgrade cycle"
        echo "  fresh                Drop all tables and reapply migrations"
        echo "  help                 Show this help message"
        echo ""
        echo "Examples:"
        echo "  $0 upgrade"
        echo "  $0 create add_user_column"
        echo "  $0 downgrade -1"
        echo "  $0 test"
        ;;
esac
