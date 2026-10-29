"""Boolean env flags, parsed one way everywhere.

`bool(os.getenv('X'))` treats `X=false` / `X=0` as on — and install.sh writes
`PIHOLE_ENABLED=false`, so a fresh install would show (and poll) Pi-hole
panels for a Pi-hole that doesn't exist.
"""
import os

_TRUE = ('1', 'true', 'yes', 'on')


def env_flag(name):
    return os.getenv(name, '').strip().lower() in _TRUE


def pihole_enabled():
    return env_flag('PIHOLE_ENABLED')
