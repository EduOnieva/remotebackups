#!/bin/bash
# Test runner script for rsync-tailscale-docker project.
# Supports both local and Docker container execution.

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Check if running inside container
if [[ -f /.dockerenv ]]; then
    RUNNING_IN_CONTAINER=true
else
    RUNNING_IN_CONTAINER=false
fi

# Function to print colored output
print_status() {
    echo -e "${BLUE}[TEST RUNNER]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if Docker is available
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed or not available"
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        print_error "Docker Compose is not installed or not available"
        exit 1
    fi
}

# Function to build test image
build_test_image() {
    print_status "Building test Docker image..."
    docker-compose build rsync-backup-test
    print_success "Test image built successfully"
}

# Function to run tests in Docker container
run_tests_in_container() {
    local test_command="$1"
    shift  # Remove first argument
    local additional_args="$@"
    
    check_docker
    
    # Create test logs directory if it doesn't exist
    mkdir -p "$PROJECT_ROOT/test-logs"
    
    print_status "Running tests in Docker container..."
    
    # Use docker-compose to run the test container with proper PYTHONPATH
    docker-compose run --rm -e PYTHONPATH=/src rsync-backup-test /run_tests.sh "$test_command" $additional_args
}

# Function to execute command based on environment
execute_test_command() {
    local command="$1"
    shift
    local args="$@"
    
    if [[ "$RUNNING_IN_CONTAINER" == "true" ]]; then
        # Running inside container - execute directly
        case "$command" in
            "unit") run_unit_tests ;;
            "integration") run_integration_tests ;;
            "e2e") run_e2e_tests ;;
            "all") run_all_tests ;;
            "coverage") run_tests_with_coverage ;;
            "quick") run_quick_tests ;;
            "slow") run_slow_tests ;;
            "specific") run_specific_test "$args" ;;
            "marker") run_tests_by_marker "$args" ;;
            *) print_error "Unknown container command: $command" ; exit 1 ;;
        esac
    else
        # Running on host - delegate to container
        run_tests_in_container "$command" "$args"
    fi
}

# Function to check if pytest is installed
check_pytest() {
    if ! command -v pytest &> /dev/null; then
        print_error "pytest is not installed. Please install it with: pip install pytest pytest-cov pytest-mock"
        exit 1
    fi
}

# Function to install test dependencies
install_deps() {
    print_status "Installing test dependencies..."
    
    # Check if requirements file exists
    if [[ -f "requirements-test.txt" ]]; then
        pip install -r requirements-test.txt
    else
        # Install basic test dependencies
        pip install pytest pytest-cov pytest-mock pytest-timeout psutil requests
    fi
    
    print_success "Test dependencies installed successfully"
}

# Function to run unit tests
run_unit_tests() {
    print_status "Running unit tests..."
    pytest tests/unit/ -v --tb=short --cov=src --cov-report=term-missing
}

# Function to run integration tests  
run_integration_tests() {
    print_status "Running integration tests..."
    pytest tests/integration/ -v --tb=short -m "not slow"
}

# Function to run end-to-end tests
run_e2e_tests() {
    print_status "Running end-to-end tests..."
    pytest tests/integration/test_e2e_workflow.py -v --tb=short
}

# Function to run all tests
run_all_tests() {
    print_status "Running complete test suite..."
    pytest tests/ -v --tb=short --cov=src --cov-report=html --cov-report=term-missing
}

# Function to run tests with coverage
run_tests_with_coverage() {
    print_status "Running tests with detailed coverage report..."
    # The threshold lives in pytest.ini so every entry point enforces the same number
    pytest tests/ --cov=src --cov-report=html --cov-report=term-missing --cov-branch
    
    if [[ -d "htmlcov" ]]; then
        print_success "Coverage report generated in htmlcov/index.html"
    fi
}

# Function to run specific test file
run_specific_test() {
    local test_file="$1"
    if [[ -z "$test_file" ]]; then
        print_error "Please specify a test file"
        exit 1
    fi
    
    if [[ ! -f "$test_file" ]]; then
        print_error "Test file not found: $test_file"
        exit 1
    fi
    
    print_status "Running specific test: $test_file"
    pytest "$test_file" -v --tb=short
}

# Function to run tests by marker
run_tests_by_marker() {
    local marker="$1"
    if [[ -z "$marker" ]]; then
        print_error "Please specify a test marker (unit, integration, slow, ssh, docker)"
        exit 1
    fi
    
    print_status "Running tests marked as: $marker"
    pytest -m "$marker" -v --tb=short
}

# Function to run quick tests (exclude slow tests)
run_quick_tests() {
    print_status "Running quick test suite (excluding slow tests)..."
    pytest tests/ -m "not slow" -v --tb=short
}

# Function to run slow tests only
run_slow_tests() {
    print_status "Running slow tests..."
    pytest -m "slow" -v --tb=short
}

# Function to check code style and run linting
run_linting() {
    print_status "Running code quality checks..."
    
    # Check if Python files exist
    if find src/ -name "*.py" | grep -q .; then
        if command -v flake8 &> /dev/null; then
            print_status "Running flake8 linting..."
            flake8 src/ --max-line-length=88 --ignore=E203,W503
        else
            print_warning "flake8 not installed, skipping linting"
        fi
        
        if command -v black &> /dev/null; then
            print_status "Checking code formatting with black..."
            black --check --diff src/
        else
            print_warning "black not installed, skipping format check"
        fi
    else
        print_warning "No Python files found in src/, skipping linting"
    fi
}

# Function to generate test report
generate_test_report() {
    print_status "Generating comprehensive test report..."
    
    # Create reports directory
    mkdir -p reports
    
    # Run tests with detailed reporting
    pytest tests/ \
        --cov=src \
        --cov-report=html:reports/coverage \
        --cov-report=xml:reports/coverage.xml \
        --cov-report=term-missing \
        --junit-xml=reports/junit.xml \
        --tb=short \
        -v
    
    print_success "Test reports generated in reports/ directory"
}

# Function to clean test artifacts
clean_test_artifacts() {
    print_status "Cleaning test artifacts..."
    
    # Remove coverage files
    rm -rf .coverage htmlcov/ reports/
    
    # Remove pytest cache
    rm -rf .pytest_cache/
    
    # Remove Python cache
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -name "*.pyc" -delete 2>/dev/null || true
    
    # Remove log files
    rm -f tests/pytest.log
    
    print_success "Test artifacts cleaned"
}

# Function to show help
show_help() {
    cat << EOF
Test Runner for rsync-tailscale-docker project

Usage: $0 [COMMAND] [OPTIONS]

Commands:
    # Container-based testing (recommended - no local dependencies needed)
    docker-build    Build test Docker image
    docker-unit     Run unit tests in container
    docker-integration  Run integration tests in container
    docker-e2e      Run end-to-end tests in container
    docker-all      Run all tests in container
    docker-coverage Run tests with coverage in container
    docker-quick    Run quick tests in container
    docker-shell    Open interactive shell in test container
    
    # Direct commands (for container execution or local setup)
    install         Install test dependencies (local only)
    unit           Run unit tests
    integration    Run integration tests
    e2e            Run end-to-end tests
    all            Run all tests
    coverage       Run tests with detailed coverage report
    quick          Run quick tests (exclude slow tests)  
    slow           Run slow tests only
    lint           Run code quality checks
    report         Generate comprehensive test report
    clean          Clean test artifacts
    
    specific <file>    Run specific test file
    marker <marker>    Run tests by marker (unit, integration, slow, ssh, docker)
    
    help           Show this help message

Recommended Usage (no local setup required):
    $0 docker-build                    # Build test environment
    $0 docker-all                      # Run all tests
    $0 docker-coverage                 # Run with coverage
    $0 docker-shell                    # Interactive testing

Local Development:
    $0 unit                            # Run unit tests
    $0 integration                     # Run integration tests  
    $0 specific tests/unit/test_log_parsing.py # Run specific test
    
Environment:
    Running in container: $RUNNING_IN_CONTAINER
    
EOF
}

# Main execution
main() {
    local command="${1:-help}"
    
    # Check if we're in the right directory (only if not in container)
    if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
        if [[ ! -f "pytest.ini" ]] || [[ ! -d "tests" ]]; then
            print_error "This script must be run from the project root directory"
            exit 1
        fi
    fi
    
    # Handle commands
    case "$command" in
        # Docker-based commands (for host execution)
        "docker-build")
            check_docker
            build_test_image
            ;;
        "docker-unit")
            execute_test_command "unit"
            ;;
        "docker-integration")
            execute_test_command "integration"
            ;;
        "docker-e2e")
            execute_test_command "e2e"
            ;;
        "docker-all")
            execute_test_command "all"
            ;;
        "docker-coverage")
            execute_test_command "coverage"
            ;;
        "docker-quick")
            execute_test_command "quick"
            ;;
        "docker-shell")
            check_docker
            print_status "Opening interactive shell in test container..."
            docker-compose run --rm rsync-backup-test /bin/bash
            ;;
        # Direct execution commands
        "install")
            if [[ "$RUNNING_IN_CONTAINER" == "true" ]]; then
                print_warning "Dependencies already installed in container"
            else
                install_deps
            fi
            ;;
        "unit")
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            run_unit_tests
            ;;
        "integration") 
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            run_integration_tests
            ;;
        "e2e")
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            run_e2e_tests
            ;;
        "all")
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            run_all_tests
            ;;
        "coverage")
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            run_tests_with_coverage
            ;;
        "quick")
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            run_quick_tests
            ;;
        "slow")
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            run_slow_tests
            ;;
        "lint")
            run_linting
            ;;
        "report")
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            generate_test_report
            ;;
        "clean")
            clean_test_artifacts
            ;;
        "specific")
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            run_specific_test "$2"
            ;;
        "marker")
            if [[ "$RUNNING_IN_CONTAINER" != "true" ]]; then
                check_pytest
            fi
            run_tests_by_marker "$2"
            ;;
        "help"|"--help"|"-h")
            show_help
            ;;
        *)
            print_error "Unknown command: $command"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"