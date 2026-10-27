"""Keep the direct inputs, hash locks and actual test environment consistent."""
from importlib.metadata import requires, version
from pathlib import Path
import re

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest

BACKEND = Path(__file__).resolve().parents[1]


def locked_requirements(filename):
    entries = {}
    current = None
    for raw in (BACKEND / filename).read_text().splitlines():
        line = raw.partition('#')[0].strip().removesuffix('\\').strip()
        if not line:
            continue
        if line.startswith('--hash='):
            assert current is not None and re.fullmatch(r'--hash=sha256:[0-9a-f]{64}', line)
            entries[current][1] += 1
        else:
            requirement = Requirement(line)
            pins = list(requirement.specifier)
            assert requirement.url is None and len(pins) == 1
            assert pins[0].operator == '==' and '*' not in pins[0].version
            current = canonicalize_name(requirement.name)
            assert current not in entries
            entries[current] = [requirement, 0]
    assert entries and all(hashes > 0 for _, hashes in entries.values())
    return {name: requirement for name, (requirement, _) in entries.items()}


@pytest.mark.parametrize('filename', ['requirements.txt', 'requirements-bootstrap.txt'])
def test_hash_lock_contains_exact_pins_matching_installed_environment(filename):
    for requirement in locked_requirements(filename).values():
        if requirement.marker is None or requirement.marker.evaluate():
            assert version(requirement.name) in requirement.specifier


def test_direct_pins_and_requested_extras_are_present_in_lock():
    locked = locked_requirements('requirements.txt')
    for raw in (BACKEND / 'requirements.in').read_text().splitlines():
        if not raw.strip() or raw.startswith('#'):
            continue
        direct = Requirement(raw)
        pinned = locked[canonicalize_name(direct.name)]
        assert str(direct.specifier) == str(pinned.specifier)
        # pip check does not verify optional extras after installation. Preserve
        # uvicorn[standard], psycopg[binary] and pwdlib[argon2] in the resolved lock.
        for dependency in requires(direct.name) or []:
            needed = Requirement(dependency)
            contexts = {'', *direct.extras}
            if needed.marker is None or any(needed.marker.evaluate({'extra': extra}) for extra in contexts):
                assert canonicalize_name(needed.name) in locked
                assert version(needed.name) in needed.specifier
