import os
import subprocess
import requests
from pathlib import Path
from dotenv import load_dotenv

# Load variables from .env file if present
load_dotenv()

SONAR_HOST = os.getenv("SONAR_HOST")
SONAR_TOKEN = os.getenv("SONAR_TOKEN")

if not SONAR_HOST or not SONAR_TOKEN:
    raise RuntimeError("Missing SONAR_HOST or SONAR_TOKEN in environment or .env file")

REPO_LIST = "repos.txt"

# Track statistics
stats = {
    "created": 0,
    "exists": 0,
    "scanned": 0,
    "failed": 0,
}

# --- Helpers ---
def to_ssh_url(repo_url: str) -> str:
    repo_url = repo_url.strip().rstrip("/")  # normalize
    if repo_url.startswith("https://github.com/"):
        return repo_url.replace("https://github.com/", "git@github.com:")
    if repo_url.startswith("https://bitbucket.org/"):
        return repo_url.replace("https://bitbucket.org/", "git@bitbucket.org:")
    return repo_url


def create_project(key, name):
    """Create project in SonarQube if it doesn't exist"""
    r = requests.get(
        f"{SONAR_HOST}/api/projects/search",
        params={"projects": key},
        auth=(SONAR_TOKEN, "")
    )
    if r.json().get("paging", {}).get("total", 0) > 0:
        print(f"[OK] Project {key} already exists.")
        stats["exists"] += 1
        return
    r = requests.post(
        f"{SONAR_HOST}/api/projects/create",
        data={"project": key, "name": name},
        auth=(SONAR_TOKEN, "")
    )
    r.raise_for_status()
    print(f"[OK] Project {key} created successfully.")
    stats["created"] += 1


def clone_or_update_repo(repo_url, repo_dir):
    """Clone the repo if missing, otherwise pull latest changes"""
    if repo_dir.exists():
        print(f"[INFO] Repo already exists at {repo_dir}, pulling latest changes...")
        try:
            subprocess.run(["git", "-C", str(repo_dir), "pull"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"[WARN] git pull failed: {e}, continuing with existing repo")
    else:
        print(f"[INFO] Cloning repo: {repo_url}")
        subprocess.run(["git", "clone", repo_url, str(repo_dir)], check=True)


def detect_and_scan(repo_dir, key, display_name):
    """Detect project type and run appropriate scan"""
    if (repo_dir / "pom.xml").exists():
        print(f"[INFO] Detected Maven project")
        cmd = [
            "mvn", "clean", "verify", "sonar:sonar",
            f"-Dsonar.projectKey={key}",
            f"-Dsonar.projectName={display_name}",
            f"-Dsonar.host.url={SONAR_HOST}",
            f"-Dsonar.login={SONAR_TOKEN}",
            "-DskipTests"  # optional: speeds things up
        ]
    elif any(repo_dir.glob("*.sln")) or any(repo_dir.glob("*.csproj")):
        print(f"[INFO] Detected .NET project")
        subprocess.run([
            "dotnet", "sonarscanner", "begin",
            f"/k:{key}",
            f"/d:sonar.host.url={SONAR_HOST}",
            f"/d:sonar.login={SONAR_TOKEN}"
        ], cwd=repo_dir, check=True)
        subprocess.run(["dotnet", "build"], cwd=repo_dir, check=True)
        cmd = [
            "dotnet", "sonarscanner", "end",
            f"/d:sonar.login={SONAR_TOKEN}"
        ]
    elif (repo_dir / "go.mod").exists():
        print(f"[INFO] Detected Go project")
        cmd = [
            "sonar-scanner",
            f"-Dsonar.projectKey={key}",
            "-Dsonar.sources=.",
            f"-Dsonar.host.url={SONAR_HOST}",
            f"-Dsonar.login={SONAR_TOKEN}",
        ]
    else:
        print(f"[INFO] No build files detected. Falling back to sonar-scanner.")
        cmd = [
            "sonar-scanner",
            f"-Dsonar.projectKey={key}",
            "-Dsonar.sources=.",
            f"-Dsonar.host.url={SONAR_HOST}",
            f"-Dsonar.login={SONAR_TOKEN}",
        ]

    subprocess.run(cmd, cwd=repo_dir, check=True)
    stats["scanned"] += 1
    print(f"[OK] Scan completed for {key}")


def main():
    with open(REPO_LIST) as f:
        for line in f:
            line = line.strip()
            if not line or "," not in line:
                continue  # skip bad lines
            prefix, repo_url = line.split(",", 1)
            repo_url = to_ssh_url(repo_url) # convert to git ssh url
            repo_name = repo_url.split("/")[-1].replace(".git", "").replace("/browse", "")

            project_key = f"{prefix}_{repo_name}"
            display_name = f"{prefix}-{repo_name}"

            print(f"\n[INFO] Processing {repo_name} ({prefix})")
            try:
                create_project(project_key, display_name)

                # Convert URL to SSH and clone/update
                repo_url = to_ssh_url(repo_url)
                repo_dir = Path("/tmp") / repo_name
                clone_or_update_repo(repo_url, repo_dir)

                detect_and_scan(repo_dir, project_key, display_name)

            except Exception as e:
                print(f"[ERROR] Failed processing {repo_name}: {e}")
                stats["failed"] += 1

    # Print summary
    print("\n=== Summary ===")
    print(f"Projects created: {stats['created']}")
    print(f"Projects already existed: {stats['exists']}")
    print(f"Repos scanned successfully: {stats['scanned']}")
    print(f"Failures: {stats['failed']}")
    print("\n[INFO] All repos processed.")


if __name__ == "__main__":
    main()
