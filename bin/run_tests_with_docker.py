#!/usr/bin/env python3
import argparse
import os
from subprocess import run

parser = argparse.ArgumentParser(description='Run tests with dockerized MongoDB')
parser.add_argument('--mongodb-version', type=str, help='MongoDB version', required=True)
# for basic and test_auth
parser.add_argument('--mongodb-port', type=str, help='MongoDB Port (default 27017)', default='27017')
# for test_replicaset
parser.add_argument('--mongodb-port-1', type=str, help='MongoDB 1 Replica Port (default 37017)', default='37017')
parser.add_argument('--mongodb-port-2', type=str, help='MongoDB 2 Replica Port (default 37018)', default='37018')
parser.add_argument('--mongodb-port-3', type=str, help='MongoDB 3 Replica Port (default 37019)', default='37019')

args, tox_args = parser.parse_known_args()

mongodb_basic_test_container_name = 'txmongo-tests-basic-mongodb'
# run(['docker', 'run', '--rm', '-d', '-p', f'{args.mongodb_port}:27017', '--name', mongodb_basic_test_container_name, f'mongo:{args.mongodb_version}'])
# run(['tox', '-e basic', *tox_args], env={
#     **os.environ,
#     'TXMONGO_RUN_MONGOD_IN_DOCKER': 'yes',
#     'TXMONGO_MONGOD_DOCKER_VERSION': args.mongodb_version,
#     'TXMONGO_MONGOD_DOCKER_PORT': args.mongodb_port,
# })
# run(['docker', 'stop', mongodb_basic_test_container_name])

mongodb_network_name = 'txmongo-tests-advanced-network'

run(['docker', 'network', 'create', mongodb_network_name])

# ports = [args.mongodb_port, args.mongodb_port_1, args.mongodb_port_2, args.mongodb_port_3]

# mongodb_container_name = 'txmongo-advanced-tests-mongodb'
#
# for i, port in enumerate(ports):
#     run([
#         'docker', 'run', '--rm', '-d',
#         '-p', f'{port}:27017',
#         '--name', f"{mongodb_container_name}-{i}",
#         '--network', mongodb_network_name,
#         f'mongo:{args.mongodb_version}'
#     ])

run(['tox', '-e advanced', *tox_args], env={
    **os.environ,
    'TXMONGO_RUN_MONGOD_IN_DOCKER': 'yes',
    'TXMONGO_MONGOD_DOCKER_VERSION': args.mongodb_version,
    'TXMONGO_MONGOD_DOCKER_PORT': args.mongodb_port,
    'TXMONGO_MONGOD_DOCKER_PORT_1': args.mongodb_port_1,
    'TXMONGO_MONGOD_DOCKER_PORT_2': args.mongodb_port_2,
    'TXMONGO_MONGOD_DOCKER_PORT_3': args.mongodb_port_3,
    'TXMONGO_MONGOD_DOCKER_NETWORK_NAME': mongodb_network_name,
})

# for i, port in enumerate(ports):
#     run(['docker', 'stop', f"{mongodb_container_name}-{i}"])

run(['docker', 'network', 'rm', "--force", mongodb_network_name])

# if need manually delete containers docker container rm --force $(docker ps -q --filter "name=txmongo-tests-")