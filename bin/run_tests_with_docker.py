#!/usr/bin/env python3
import argparse
import os
from subprocess import run

parser = argparse.ArgumentParser(description='Run tests with dockerized MongoDB')
parser.add_argument('--mongodb-version', type=str, help='MongoDB version', required=True)

args, tox_args = parser.parse_known_args()

mongodb_container_name = 'txmongo-basic-tests-mongodb'

run(['docker', 'run', '--rm', '-d', '-p', '27017:27017', '--name', mongodb_container_name, f'mongo:{args.mongodb_version}'])
run(['tox', *tox_args], env={
    **os.environ,
    'TXMONGO_RUN_MONGOD_IN_DOCKER': 'yes',
    'TXMONGO_MONGOD_DOCKER_VERSION': args.mongodb_version,
})
run(['docker', 'stop', mongodb_container_name])
