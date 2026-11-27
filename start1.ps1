python init_project.py --rotate

docker compose -f vm1/docker-compose.yml down
docker compose -f vm2/docker-compose.yml down

docker volume rm factory-vm1_postgres-auth-data factory-vm1_clickhouse-data factory-vm1_redis-langfuse-data
docker volume rm factory-vm2_postgres-runtime-data factory-vm2_redis-factory-data

docker compose -f vm2/docker-compose.yml up -d
docker compose -f vm1/docker-compose.yml up -d