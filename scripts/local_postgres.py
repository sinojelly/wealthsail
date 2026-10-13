"""Portable Windows PostgreSQL, kept entirely inside the local project."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import zipfile

POSTGRES_URL = "https://get.enterprisedb.com/postgresql/postgresql-16.10-1-windows-x64-binaries.zip"
REQUIRED_TOOLS = ("postgres.exe", "pg_ctl.exe", "initdb.exe")


def extract_postgres(archive: Path, target: Path) -> None:
    """Keep runtime files; omit build headers, static libraries and symbols."""
    target = target.resolve()
    with zipfile.ZipFile(archive) as handle:
        selected = [entry for entry in handle.infolist()
                    if entry.filename.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/"))
                    and not entry.filename.lower().endswith((".pdb", ".lib", ".a"))]
        for entry in selected:
            if not (target / entry.filename).resolve().is_relative_to(target):
                raise ValueError("PostgreSQL archive entry resolves outside its destination")
        for entry in selected:
            handle.extract(entry, target)


def _run(command: list[str], env: dict[str, str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if check and result.returncode:
        raise RuntimeError(result.stdout.strip() or f"PostgreSQL command failed: {command[0]}")
    return result


def postgres_bin(local: Path, supplied: Path | None, env: dict[str, str]) -> Path:
    binary_dir = supplied.resolve() if supplied else local / "postgres/pgsql/bin"
    if not all((binary_dir / name).is_file() for name in REQUIRED_TOOLS):
        if supplied:
            raise RuntimeError(f"PostgresBin must contain postgres.exe, pg_ctl.exe and initdb.exe: {binary_dir}")
        archive = local / "tmp/postgresql-16.zip"
        if not shutil.which("curl.exe"):
            raise RuntimeError("Windows curl.exe is required to download the portable PostgreSQL archive")
        print("Downloading portable PostgreSQL 16 (~308 MiB) to the project drive...", flush=True)
        # TLS verification stays enabled; the URL is the EDB archive linked by
        # PostgreSQL's official Windows download page. No system installation.
        subprocess.run(["curl.exe", "--fail", "--location", "--retry", "2", "--max-time", "600",
                        "--silent", "--show-error", "--output", str(archive), POSTGRES_URL],
                       env=env, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        print("Extracting PostgreSQL runtime files...", flush=True)
        extract_postgres(archive, local / "postgres")
        archive.unlink()  # Release the download space once extraction succeeded.
    version = _run([str(binary_dir / "pg_ctl.exe"), "--version"], env).stdout.strip()
    if "(PostgreSQL) 16." not in version:
        raise RuntimeError(f"Use portable PostgreSQL 16.x for this local launcher; found {version}")
    return binary_dir


def database_running(binary_dir: Path, local: Path, env: dict[str, str]) -> bool:
    data = local / "pgdata"
    if not (data / "PG_VERSION").exists():
        return False
    return _run([str(binary_dir / "pg_ctl.exe"), "-D", str(data), "status"], env, check=False).returncode == 0


def start_database(binary_dir: Path, local: Path, port: int, password: str,
                   env: dict[str, str]) -> None:
    data = local / "pgdata"
    if not (data / "PG_VERSION").exists():
        password_file = local / "tmp/postgres-password.txt"
        password_file.write_text(password, encoding="utf-8")
        try:
            print("Initializing portable PostgreSQL data directory...", flush=True)
            _run([str(binary_dir / "initdb.exe"), "-D", str(data), "-U", "postgres",
                  f"--pwfile={password_file}", "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C"], env)
        finally:
            password_file.unlink(missing_ok=True)
    if (data / "PG_VERSION").read_text("utf-8").strip() != "16":
        raise RuntimeError("Existing .local/pgdata must use PostgreSQL 16; do not overwrite another cluster")
    if database_running(binary_dir, local, env):
        current_port = int((data / "postmaster.pid").read_text("utf-8").splitlines()[3])
        if current_port != port:
            raise RuntimeError(f"Local PostgreSQL already uses port {current_port}; stop it before changing -DbPort")
        return
    print(f"Starting portable PostgreSQL on 127.0.0.1:{port}...", flush=True)
    command = [str(binary_dir / "pg_ctl.exe"), "-D", str(data), "-l", str(local / "logs/postgres.log"),
               "-w", "-t", "90", "-o", f"-p {port} -h 127.0.0.1 -c timezone=Asia/Shanghai", "start"]
    # A persistent Windows postgres child can inherit pg_ctl's stdout handle.
    # A PIPE would make communicate() wait for the server's entire lifetime.
    # Use a file and wait only for pg_ctl itself to finish its readiness check.
    control_log = local / "logs/pg_ctl.log"
    with control_log.open("a", encoding="utf-8") as output:
        result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(control_log.read_text("utf-8", errors="replace")[-4000:])


def stop_database(binary_dir: Path, local: Path, env: dict[str, str]) -> None:
    if database_running(binary_dir, local, env):
        _run([str(binary_dir / "pg_ctl.exe"), "-D", str(local / "pgdata"), "-m", "fast", "-w", "stop"], env)
