import os
import subprocess
import requests
from pathlib import Path
from dotenv import load_dotenv
import argparse
from datetime import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import json

# Load environment from .env
load_dotenv()
SONAR_HOST = os.getenv("SONAR_HOST")
SONAR_TOKEN = os.getenv("SONAR_TOKEN")
REPO_LIST = "repos.txt"
LOCAL_MODE = False
MAVEN_LOCAL_REPO = Path("./tmp/local-m2")
REQUEST_TIMEOUT = 10
MAX_WORKERS_DEFAULT = 2
EXTRA_COMMANDS_PATH = Path("extra_commands.json")
JACOCO_VERSION = "0.8.11"
JACOCO_PLUGIN = f"org.jacoco:jacoco-maven-plugin:{JACOCO_VERSION}"

if not SONAR_HOST or not SONAR_TOKEN:
    raise RuntimeError("Missing SONAR_HOST or SONAR_TOKEN environment variables")

# Stats
stats = {"created": 0, "exists": 0, "scanned": 0, "config_only": 0, "empty": 0, "failed": 0}
stats_lock = threading.Lock()
thread_local = threading.local()
extra_commands = {}


def get_session():
    """Thread-local requests session with connection pooling + retries."""
    if not hasattr(thread_local, "session"):
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=3)
        s = requests.Session()
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        thread_local.session = s
    return thread_local.session


def bump_stat(key: str, inc: int = 1):
    with stats_lock:
        stats[key] += inc


def load_extra_commands():
    if not EXTRA_COMMANDS_PATH.exists():
        return {}
    try:
        with open(EXTRA_COMMANDS_PATH) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            print(f"[WARN] {EXTRA_COMMANDS_PATH} is not a mapping, ignoring.")
            return {}
    except Exception as e:
        print(f"[WARN] Failed to load {EXTRA_COMMANDS_PATH}: {e}")
        return {}


# ======================================================================
# URL NORMALIZATION
# ======================================================================
def to_ssh_url(url: str) -> str:
    url = url.strip().rstrip("/")

    # Bitbucket/Stash
    if "stash.haesoft.net" in url:
        url = url.replace("/browse", "").replace("/browse/", "/")
        if "/projects/" in url and "/repos/" in url:
            proj = url.split("/projects/")[1].split("/")[0]
            repo = url.split("/repos/")[1].split("/")[0].replace(".git", "")
            return f"ssh://git@stash.haesoft.net:7999/{proj}/{repo}.git"
        raise ValueError(f"Invalid Bitbucket URL: {url}")

    # GitHub
    if url.startswith("https://github.com/"):
        url = url.replace("https://github.com/", "git@github.com:")
        return url if url.endswith(".git") else url + ".git"

    return url


# ======================================================================
# SONARQUBE PROJECT CREATION
# ======================================================================
def create_project(key, name):
    session = get_session()
    r = session.get(
        f"{SONAR_HOST}/api/projects/search",
        params={"projects": key},
        auth=(SONAR_TOKEN, ""),
        timeout=REQUEST_TIMEOUT,
    )

    if r.json().get("paging", {}).get("total", 0) > 0:
        print(f"[OK] Project exists → {key}")
        bump_stat("exists")
        return

    session.post(
        f"{SONAR_HOST}/api/projects/create",
        data={"project": key, "name": name},
        auth=(SONAR_TOKEN, ""),
        timeout=REQUEST_TIMEOUT,
    ).raise_for_status()

    print(f"[OK] Created project → {key}")
    bump_stat("created")


def rename_default_branch(project_key, branch):
    try:
        session = get_session()
        session.post(
            f"{SONAR_HOST}/api/project_branches/rename",
            params={"project": project_key, "name": branch},
            auth=(SONAR_TOKEN, ""),
            timeout=REQUEST_TIMEOUT,
        )
        print(f"[INFO] Default branch set → {branch}")
    except Exception:
        print(f"[WARN] Branch rename failed or not needed: {project_key}")


# ======================================================================
# GIT
# ======================================================================
def clone_or_update_repo(url, repo_dir: Path):
    if repo_dir.exists():
        print(f"[INFO] Pulling latest → {repo_dir}")
        subprocess.run(["git", "-C", str(repo_dir), "pull"], check=False)
    else:
        print(f"[INFO] Cloning → {url}")
        subprocess.run(["git", "clone", url, str(repo_dir)], check=True)


def detect_branch(repo_dir: Path) -> str:
    try:
        branches = subprocess.check_output(
            ["git", "-C", str(repo_dir), "branch", "-a"], text=True
        ).lower()
        if "main" in branches:
            return "main"
        if "master" in branches:
            return "master"
    except Exception:
        pass

    try:
        return (
            subprocess.check_output(
                ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                text=True,
            ).strip()
            or "main"
        )
    except Exception:
        return "main"


def checkout_default_branch(repo_dir: Path) -> str:
    branch = detect_branch(repo_dir)
    print(f"[INFO] Checking out: {branch}")
    subprocess.run(["git", "-C", str(repo_dir), "checkout", branch], check=False)
    return branch


# ======================================================================
# TYPE CLASSIFICATION
# ======================================================================
def classify_repo(repo_dir: Path):
    code_ext = (".java", ".py", ".js", ".ts", ".go", ".cs", ".cpp", ".rb", ".php")
    yaml_ext = (".yaml", ".yml")
    tf_ext = (".tf",)

    has_yaml = False
    has_tf = False

    for f in repo_dir.rglob("*"):
        if not f.is_file():
            continue

        suffix = f.suffix.lower()
        if suffix == ".jmx":
            return "jmeter", {"jmeter", "performance"}
        if suffix in code_ext:
            return "code", {"code"}
        if suffix in yaml_ext:
            has_yaml = True
        if suffix in tf_ext:
            has_tf = True

    tags = set()
    if has_yaml or has_tf:
        tags.add("config")
        if has_yaml:
            tags.add("yaml")
        if has_tf:
            tags.add("terraform")
        return "config", tags

    return "empty", tags


def apply_extra_commands(repo_root: Path, repo_name: str) -> Path:
    cfg = extra_commands.get(repo_name)
    if not cfg:
        return repo_root

    workdir = cfg.get("workdir")
    commands = cfg.get("commands") or []

    scan_dir = repo_root / workdir if workdir else repo_root
    if workdir:
        if scan_dir.exists():
            print(f"[EXTRA] Using workdir '{workdir}' for {repo_name}")
        else:
            print(f"[WARN] workdir '{workdir}' missing for {repo_name} — falling back to repo root")
            scan_dir = repo_root

    if commands:
        script = "set -e\n" + "\n".join(commands)
        print(f"[EXTRA] Running {len(commands)} command(s) for {repo_name}")
        subprocess.run(["bash", "-lc", script], cwd=scan_dir, check=True)

    return scan_dir


# ======================================================================
# SCANNING HELPERS
# ======================================================================
def run_scanner(cmd, repo_dir):
    subprocess.run(cmd, cwd=repo_dir, check=True)
    bump_stat("scanned")


def run_maven_build(repo_dir: Path):
    """Build + test Java projects; honor offline local cache when in local mode."""
    cmd = [
        "mvn",
        "clean",
        f"{JACOCO_PLUGIN}:prepare-agent",
        "test",
        f"{JACOCO_PLUGIN}:report",
        "package",
        "-DskipITs",
    ]

    if LOCAL_MODE:
        MAVEN_LOCAL_REPO.mkdir(parents=True, exist_ok=True)
        cmd.append(f"-Dmaven.repo.local={MAVEN_LOCAL_REPO}")
        cmd.append("-o")

    print(f"[JAVA] Build + coverage → {' '.join(cmd)}")
    subprocess.run(cmd, cwd=repo_dir, check=True)


def find_java_binaries(repo_dir: Path) -> list[str]:
    # Common Maven output locations; collect any that exist
    candidates = [p for p in repo_dir.rglob("target/classes") if p.is_dir()]
    return [str(p) for p in candidates]


def find_jacoco_reports(repo_dir: Path) -> list[str]:
    reports = [
        p for p in repo_dir.rglob("target/site/**/jacoco*.xml")
        if p.is_file()
    ]
    return [str(p) for p in reports]


# ======================================================================
# .NET HELPERS — Full SonarScanner for .NET flow
# ======================================================================
def find_csproj_files(repo_dir: Path):
    return list(repo_dir.rglob("*.csproj"))


def generate_csproj(repo_dir: Path, name: str = "TempScannerProject") -> Path:
    """Create a temporary buildable .csproj when there is C# code but no project."""
    import uuid

    csproj = repo_dir / f"{name}.csproj"
    guid = uuid.uuid4()

    content = f"""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <AssemblyName>{name}</AssemblyName>
    <RootNamespace>{name}</RootNamespace>
    <ProjectGuid>{{{guid}}}</ProjectGuid>
    <Nullable>enable</Nullable>
    <LangVersion>latest</LangVersion>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="**/*.cs" Exclude="bin/**;obj/**;*.Designer.cs" />
  </ItemGroup>
</Project>
"""
    csproj.write_text(content)
    print(f"[.NET] ✔ Temp project created → {csproj.name}")
    return csproj


def generate_temp_solution(repo_dir: Path) -> Path:
    """Create a solution and add all existing (or generated) csproj files."""
    sln_name = "sonar-temp"
    sln_path = repo_dir / f"{sln_name}.sln"

    # Create solution
    subprocess.run(["dotnet", "new", "sln", "-n", sln_name], cwd=repo_dir, check=True)
    print(f"[.NET] Created solution → {sln_path.name}")

    csproj_files = find_csproj_files(repo_dir)

    # If repo has no projects but has C# files → generate one
    if not csproj_files and any(repo_dir.rglob("*.cs")):
        csproj = generate_csproj(repo_dir)
        csproj_files = [csproj]

    for csproj in csproj_files:
        rel = os.path.relpath(csproj, repo_dir)
        print(f"[.NET] ➕ Linking {rel} into solution")
        subprocess.run(["dotnet", "sln", sln_path.name, "add", rel], cwd=repo_dir, check=True)

    # Optional: list contents for debug
    print("\n[.NET DEBUG] Solution contents:")
    subprocess.run(["dotnet", "sln", sln_path.name, "list"], cwd=repo_dir)

    return sln_path


def scan_dotnet(repo_dir: Path, key: str, name: str):
    print("[.NET] Detected → scanning with dotnet-sonarscanner")

    # Prepare solution (create if missing + attach csproj files)
    sln_files = list(repo_dir.glob("*.sln"))
    if sln_files:
        sln = sln_files[0]
        print(f"[.NET] Using existing solution → {sln.name}")
    else:
        print("[.NET] No solution found → generating one and linking csproj files")
        sln = generate_temp_solution(repo_dir)
        for csproj in find_csproj_files(repo_dir):
            rel = os.path.relpath(csproj, repo_dir)
            print(f"[.NET] Linking project → {rel}")
            subprocess.run(["dotnet","sln",sln.name,"add",rel], cwd=repo_dir, check=False)

    try:
        coverage_glob = "**/TestResults/coverage.opencover.xml"
        trx_glob = "**/TestResults/*.trx"

        # Sonar begin
        subprocess.run(["dotnet","sonarscanner","begin",
            f"/k:{key}",
            f"/n:{name}",
            f"/d:sonar.host.url={SONAR_HOST}",
            f"/d:sonar.token={SONAR_TOKEN}",
            "/d:sonar.scanner.skipJreProvisioning=true",
            f"/d:sonar.cs.vstest.reportsPaths={trx_glob}",
            f"/d:sonar.cs.opencover.reportsPaths={coverage_glob}",
        ], cwd=repo_dir, check=True)

        # Required even if build fails
        subprocess.run(["dotnet","restore",sln.name], cwd=repo_dir, check=False)

        # Allow build to fail but keep scanning 🚀
        try:
            subprocess.run(["dotnet","build",sln.name,"--no-incremental"], cwd=repo_dir, check=True)
        except Exception:
            print("[WARN] Build failed — continuing with Sonar analysis anyway")

        # Tests + coverage (best effort)
        try:
            subprocess.run([
                "dotnet","test",sln.name,"--no-build",
                "/p:CollectCoverage=true",
                "/p:CoverletOutputFormat=opencover",
                "/p:CoverletOutput=TestResults/coverage.opencover.xml",
                "/logger:trx"
            ], cwd=repo_dir, check=True)
        except Exception:
            print("[WARN] dotnet test failed — coverage may be missing")

        # Finalize scan whether build worked or not
        subprocess.run(["dotnet","sonarscanner","end",
            f"/d:sonar.token={SONAR_TOKEN}"
        ], cwd=repo_dir, check=False)

        bump_stat("scanned")
        print(f"[OK] .NET scan completed (build optional) → {key}")
        return

    except Exception as e:
        print(f"[ERROR] Sonar .NET pipeline crashed → {e}")
        print("[FALLBACK] Running generic scanner without MSBuild")
        return run_scanner([
            "sonar-scanner",
            f"-Dsonar.projectKey={key}",
            f"-Dsonar.projectName={name}",
            "-Dsonar.sources=.",
            f"-Dsonar.host.url={SONAR_HOST}",
            f"-Dsonar.token={SONAR_TOKEN}",
        ], repo_dir)


def scan_java(repo_dir: Path, key: str, name: str):
    print("[JAVA] Maven scanning")
    binaries = []
    coverage_reports = []

    try:
        run_maven_build(repo_dir)
        binaries = find_java_binaries(repo_dir)
        coverage_reports = find_jacoco_reports(repo_dir)
    except Exception as e:
        print(f"[ERROR] Maven build or tests failed, skipping Sonar scan → {e}")
        raise RuntimeError(f"Maven build failed: {e}")

    if not binaries:
        raise RuntimeError("Missing compiled classes after build; cannot run Sonar Java analysis")

    sonar_cmd = [
        "mvn",
        "sonar:sonar",
        f"-Dsonar.projectKey={key}",
        f"-Dsonar.projectName={name}",
        f"-Dsonar.host.url={SONAR_HOST}",
        f"-Dsonar.login={SONAR_TOKEN}",  # Maven still uses sonar.login
        f"-Dsonar.java.binaries={','.join(binaries)}",
    ]

    if LOCAL_MODE:
        sonar_cmd.append(f"-Dmaven.repo.local={MAVEN_LOCAL_REPO}")

    if coverage_reports:
        sonar_cmd.append(f"-Dsonar.coverage.jacoco.xmlReportPaths={','.join(coverage_reports)}")
    else:
        print("[WARN] Jacoco coverage not found — coverage will be absent")

    result = run_scanner(sonar_cmd, repo_dir)
    print(f"[OK] Java scan completed → {key}")
    return result


def scan_python(repo_dir: Path, key: str, name: str):
    print("[PYTHON] Detected → running pytest with coverage (best effort)")
    coverage_file = repo_dir / "coverage.xml"

    try:
        subprocess.run([
            "python",
            "-m",
            "pytest",
            "--maxfail=1",
            "--disable-warnings",
            "--cov=.",
            "--cov-report",
            f"xml:{coverage_file}"
        ], cwd=repo_dir, check=True)
    except Exception as e:
        print(f"[WARN] Pytest or coverage failed → {e}")

    sonar_cmd = [
        "sonar-scanner",
        f"-Dsonar.projectKey={key}",
        f"-Dsonar.projectName={name}",
        "-Dsonar.sources=.",
        "-Dsonar.tests=.",
        "-Dsonar.test.inclusions=**/test_*.py,**/*_test.py",
        "-Dsonar.exclusions=**/__pycache__/**,**/*.pyc",
        f"-Dsonar.host.url={SONAR_HOST}",
        f"-Dsonar.token={SONAR_TOKEN}",
    ]

    if coverage_file.exists():
        sonar_cmd.append(f"-Dsonar.python.coverage.reportPaths={coverage_file}")
    else:
        print("[WARN] Python coverage file missing — coverage will be absent")

    result = run_scanner(sonar_cmd, repo_dir)
    print(f"[OK] Python scan completed → {key}")
    return result


def scan_go(repo_dir: Path, key: str, name: str):
    print("[GO] Detected → running go test with coverage (best effort)")
    coverage_file = repo_dir / "coverage.out"

    try:
        subprocess.run(
            ["go", "test", "./...", "-coverprofile=coverage.out", "-covermode=atomic"],
            cwd=repo_dir,
            check=True,
        )
    except Exception as e:
        print(f"[WARN] Go tests or coverage failed → {e}")

    sonar_cmd = [
        "sonar-scanner",
        f"-Dsonar.projectKey={key}",
        f"-Dsonar.projectName={name}",
        "-Dsonar.sources=.",
        "-Dsonar.tests=.",
        "-Dsonar.test.inclusions=**/*_test.go",
        f"-Dsonar.host.url={SONAR_HOST}",
        f"-Dsonar.token={SONAR_TOKEN}",
    ]

    if coverage_file.exists():
        sonar_cmd.append(f"-Dsonar.go.coverage.reportPaths={coverage_file}")
    else:
        print("[WARN] Go coverage file missing — coverage will be absent")

    result = run_scanner(sonar_cmd, repo_dir)
    print(f"[OK] Go scan completed → {key}")
    return result


# ======================================================================
# DETECT + SCAN
# ======================================================================
def detect_and_scan(repo_dir: Path, key: str, name: str, repo_root: Path | None = None):
    repo_root = repo_root or repo_dir
    branch = checkout_default_branch(repo_root)
    rename_default_branch(key, branch)

    repo_type, tags = classify_repo(repo_dir)
    print(f"[INFO] Type={repo_type} Tags={','.join(tags) if tags else ''}")

    # ------------------- JMETER --------------------------
    if repo_type == "jmeter":
        new_name = f"{name} (config-only)"

        print(f"[JMX] Performance test repo detected → tagging as config-only")
        print(f"[INFO] Updating SonarQube project name → {new_name}")

        # rename SONARQUBE PROJECT TITLE
        try:
            session = get_session()
            session.post(
                f"{SONAR_HOST}/api/projects/update",
                data={
                    "project": key,
                    "name": new_name,                  # unchanged
                    "description": "JMX only repo — no source code to compile, configuration scanning enabled."
                },
                auth=(SONAR_TOKEN, ""),
                timeout=REQUEST_TIMEOUT,
            )

        except Exception as e:
            print("[WARN] Unable to rename project:", e)

        return run_scanner(
            [
                "sonar-scanner",
                f"-Dsonar.projectKey={key}",
                f"-Dsonar.projectName={new_name}",
                "-Dsonar.sources=.",
                "-Dsonar.inclusions=**/*.jmx,**/*.xml,**/*.properties",
                "-Dsonar.exclusions=results/**,logs/**,output/**",
                "-Dsonar.iac.enable=true",
                "-Dsonar.import_unknown_files=true",
                f"-Dsonar.host.url={SONAR_HOST}",
                f"-Dsonar.token={SONAR_TOKEN}",
            ],
            repo_dir,
        )

    # ------------------- CONFIG MODE -------------------
    if repo_type == "config":
        print("[CONFIG] Running config scan")
        bump_stat("config_only")
        return run_scanner(
            [
                "sonar-scanner",
                f"-Dsonar.projectKey={key}",
                f"-Dsonar.projectName={name}",
                "-Dsonar.sources=.",
                "-Dsonar.inclusions=**/*.yaml,**/*.yml,**/*.tf",
                "-Dsonar.iac.enable=true",
                f"-Dsonar.host.url={SONAR_HOST}",
                f"-Dsonar.token={SONAR_TOKEN}",
            ],
            repo_dir,
        )

    # ------------------- EMPTY -------------------------
    if repo_type == "empty":
        print("[SKIP] No code present")
        bump_stat("empty")
        return

    # ------------------- JAVA --------------------------
    if (repo_dir / "pom.xml").exists():
        return scan_java(repo_dir, key, name)

    # ------------------- .NET (Full SonarScanner for .NET) ----------------
    if find_csproj_files(repo_dir) or any(repo_dir.rglob("*.cs")):
        return scan_dotnet(repo_dir, key, name)

    # ------------------- PYTHON ------------------------
    if any(repo_dir.rglob("*.py")):
        return scan_python(repo_dir, key, name)

    # ------------------- GO ----------------------------
    if (repo_dir / "go.mod").exists() or any(repo_dir.rglob("*.go")):
        return scan_go(repo_dir, key, name)

    # ---------------- GENERIC --------------------------
    print("[GENERIC] Running fallback scanner")
    return run_scanner(
        [
            "sonar-scanner",
            f"-Dsonar.projectKey={key}",
            f"-Dsonar.projectName={name}",
            "-Dsonar.sources=.",
            f"-Dsonar.host.url={SONAR_HOST}",
            f"-Dsonar.token={SONAR_TOKEN}",
        ],
        repo_dir,
    )


def run_jobs(jobs, workers: int):
    """Execute jobs concurrently; fall back to serial when workers<=1."""
    workers = max(1, workers)
    if workers == 1 or len(jobs) <= 1:
        for job in jobs:
            job()
        return

    print(f"[INFO] Running with up to {workers} concurrent workers")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(job) for job in jobs]
        for future in as_completed(futures):
            if future.exception():
                print(f"[WARN] Worker error: {future.exception()}")


def process_local_repo(repo_dir: Path):
    repo = repo_dir.name
    key = repo.replace("-", "_")
    print(f"\n🔍 Scanning local repo → {repo}")
    try:
        create_project(key, repo)
        scan_dir = apply_extra_commands(repo_dir, repo)
        detect_and_scan(scan_dir, key, repo, repo_root=repo_dir)
    except Exception as e:
        print(f"[ERR] {repo}: {e}")
        bump_stat("failed")


def process_remote_repo(prefix: str, url: str, remote_base: Path):
    ssh = to_ssh_url(url)
    repo = ssh.split("/")[-1].replace(".git", "")
    key = f"{prefix}_{repo}"
    dest = remote_base / repo

    print(f"\n📥 Cloning/Scanning → {repo}")
    try:
        create_project(key, f"{prefix}-{repo}")
        clone_or_update_repo(ssh, dest)
        scan_dir = apply_extra_commands(dest, repo)
        detect_and_scan(scan_dir, key, f"{prefix}-{repo}", repo_root=dest)
    except Exception as e:
        print(f"[ERR] {repo}: {e}")
        bump_stat("failed")


# ======================================================================
# CLI ARGUMENTS
# ======================================================================
def parse_args():
    p = argparse.ArgumentParser(description="Bulk SonarQube repo scanner")
    p.add_argument(
        "--local-repos",
        action="store_true",
        help="Scan repos in ./tmp/repos instead of pulling remote",
    )
    p.add_argument(
        "--repo-list",
        default=REPO_LIST,
        help="Path to repo list file",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS_DEFAULT,
        help="Concurrent scans (default: half of CPU cores)",
    )
    return p.parse_args()


# ======================================================================
# MAIN
# ======================================================================
def main():
    args = parse_args()
    global LOCAL_MODE
    LOCAL_MODE = args.local_repos
    workers = max(1, args.workers)
    global extra_commands
    extra_commands = load_extra_commands()

    print("\n=== SonarQube Repo Scanner ===")
    print(f"Date: {datetime.now().isoformat(timespec='seconds')}")
    print(f"Server: {SONAR_HOST}")

    local_base = Path("./tmp/repos")
    remote_base = Path("/tmp")

    # ---------------- LOCAL MODE ----------------
    if args.local_repos:
        print("\n=== LOCAL MODE: ./tmp/repos ===")
        MAVEN_LOCAL_REPO.mkdir(parents=True, exist_ok=True)
        if not local_base.exists():
            print("[ERROR] Missing ./tmp/repos")
            return

        jobs = []
        for repo_dir in [d for d in local_base.iterdir() if d.is_dir()]:
            jobs.append(lambda repo_dir=repo_dir: process_local_repo(repo_dir))

        run_jobs(jobs, workers)

    # ---------------- REMOTE MODE ----------------
    else:
        print("\n=== REMOTE MODE: loading repo list ===")
        remote_base.mkdir(parents=True, exist_ok=True)
        jobs = []
        with open(args.repo_list) as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    prefix, url = line.strip().split(",", 1)
                except ValueError:
                    print(f"[WARN] Skipping malformed line: {line.strip()}")
                    continue

                jobs.append(lambda prefix=prefix, url=url: process_remote_repo(prefix, url, remote_base))

        run_jobs(jobs, workers)

    print("\n====== SUMMARY ======")
    print(stats)
    print("=====================")


if __name__ == "__main__":
    main()
