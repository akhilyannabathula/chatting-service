### to build docker image `docker build -t fastapi-chat-service-image .`
### to run `docker run -d -p 80:80 --name containerName fastapi-chat-service-image`
### to run one more `docker run -d -p 8080:80 --name containerName2 fastapi-chat-service-image`
### to run redis `docker run -d --name redis-stack -p 6379:6379 -p 8001:8001 redis/redis-stack:latest`# chatting-service
