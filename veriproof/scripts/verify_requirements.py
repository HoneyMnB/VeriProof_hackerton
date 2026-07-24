"""requirements.txt에 선언된 직접 의존성의 설치·버전을 검증한다."""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import Version


def _requirements(path: Path) -> list[Requirement]:
    """주석과 pip 옵션을 제외한 직접 의존성 선언만 읽는다."""
    requirements = []
    for line in path.read_text(encoding="utf-8").splitlines():
        declaration = line.split("#", 1)[0].strip()
        if not declaration or declaration.startswith("-"):
            continue
        try:
            requirements.append(Requirement(declaration))
        except InvalidRequirement as exc:
            raise ValueError(f"Invalid requirement declaration: {line}") from exc
    return requirements


def main() -> int:
    """requirements.txt의 각 의존성을 점검해 누락/버전 불일치를 보고하고 결과 코드를 반환한다."""
    requirements_file = Path(__file__).resolve().parents[1] / "requirements.txt"
    missing: list[str] = []
    incompatible: list[str] = []

    for requirement in _requirements(requirements_file):
        try:
            installed = Version(version(requirement.name))
        except PackageNotFoundError:
            missing.append(str(requirement))
            continue
        if requirement.specifier and installed not in requirement.specifier:
            incompatible.append(f"{requirement.name} {installed} (requires {requirement.specifier})")

    if missing or incompatible:
        print("Required Python packages are not ready:", file=sys.stderr)
        for item in missing:
            print(f"  missing: {item}", file=sys.stderr)
        for item in incompatible:
            print(f"  incompatible: {item}", file=sys.stderr)
        print(
            f"Install them with: {sys.executable} -m pip install -r {requirements_file}",
            file=sys.stderr,
        )
        return 1

    print(f"Python requirements verified: {requirements_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
