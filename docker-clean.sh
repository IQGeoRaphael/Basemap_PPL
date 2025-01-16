#!/bin/bash

case "$1" in
    -c|--containers)
        echo "Cleaning only containers..."
        docker stop $(docker ps -aq) 2>/dev/null || true
        docker rm $(docker ps -aq) 2>/dev/null || true
        ;;
    
    -i|--images)
        echo "Cleaning only images..."
        docker rmi $(docker images -q) -f 2>/dev/null || true
        ;;
        
    -v|--volumes)
        echo "Cleaning only volumes..."
        docker volume rm $(docker volume ls -q) 2>/dev/null || true
        ;;
        
    -n|--networks)
        echo "Cleaning only networks..."
        # Remove only user-defined networks, ignore pre-defined ones
        docker network ls --filter type=custom -q | xargs -r docker network rm 2>/dev/null || true
        ;;
        
    -a|--all|"")
        echo "Cleaning everything..."
        docker stop $(docker ps -aq) 2>/dev/null || true
        docker rm $(docker ps -aq) 2>/dev/null || true
        docker rmi $(docker images -q) -f 2>/dev/null || true
        docker volume rm $(docker volume ls -q) 2>/dev/null || true
        # Remove only user-defined networks
        docker network ls --filter type=custom -q | xargs -r docker network rm 2>/dev/null || true
        docker system prune -a --volumes --force
        ;;
        
    -h|--help)
        echo "Docker cleanup utility"
        echo "Usage: docker-clean [option]"
        echo "Options:"
        echo "  -c, --containers  Clean only containers"
        echo "  -i, --images      Clean only images"
        echo "  -v, --volumes     Clean only volumes"
        echo "  -n, --networks    Clean only networks (except pre-defined ones)"
        echo "  -a, --all         Clean everything (default)"
        echo "  -h, --help        Show this help message"
        ;;
        
    *)
        echo "Unknown option: $1"
        echo "Use docker-clean --help for usage information"
        ;;
esac