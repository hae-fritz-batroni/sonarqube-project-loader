# Sonar Project Loader

Automates the onboarding of multiple repositories into a **SonarQube (Community Edition)** server.
This script reads a mapping of application prefixes and repo URLs, creates projects in SonarQube (if they don’t already exist), and triggers scans using the appropriate CLI tool for the detected language/framework.

---

## Features

* Bulk **project creation** in SonarQube via REST API.
* **Prefix-based naming** for better organization (e.g., `NLD_repoName`).
* **Language detection**:

  * Java (Maven/Gradle)
  * .NET (sln/csproj)
  * Go (go.mod)
  * Fallback to generic `sonar-scanner` for other languages.
* Supports both **GitHub** and **Bitbucket/Stash** repos.
* Graceful failure handling and logging with a **summary report**.

---

## Repository Structure

```
sonar-project-loader/
├── add_repos.py     # Main automation script
├── repos.txt        # Repo mapping file (prefix,repo_url)
├── requirements.txt # Python dependencies
├── setup_env.sh     # Environment setup script (Ubuntu only)
├── example.log      # Sample run log output
└── README.md
```

---

## Prerequisites

* **SonarQube server** (Community Edition or above)
* **Admin token** from SonarQube
* **Ubuntu server** (script assumes Ubuntu for package installs)

The setup script will automatically install and configure:

* **Python virtual environment** with dependencies (`requests`, `python-dotenv`)
* **OpenJDK 17** (for Java projects)
* **Maven** (Java build/scan support)
* **.NET SDK 7.0** (for .NET projects)
* **Go 1.22+** (for Go projects)
* **Sonar Scanner CLI** (fallback scanner)
* **git** (for cloning repositories)

---

## Environment Variables

Set these before running:

```bash
export SONAR_HOST="http://<your-sonarqube-host>:9000"
export SONAR_TOKEN="<your-admin-token>"
```

> Tip: you can place them in a `.env` file and use `python-dotenv` to load them automatically.
***NOTE: Make sure your .env file is ignored by git in .gitignore file

---

## Environment Setup (Ubuntu Only)

Run the setup script to install dependencies and tools:

```bash
./setup_env.sh
```

Then activate and run:

```bash
source .venv/bin/activate
python add_repos.py
```

---

## Repo Mapping File (`repos.txt`)

Each line contains an application prefix (e.g., app name or domain) and repo URL:

```text
NexLynkDMS,https://github.com/hae-rnd-plasma-software/pl-nldms-main
TEGManager,https://github.com/hae-rnd-hospital-software/tegm
HaemoCloud,https://stash.haesoft.net/projects/CLD/repos/lambda/browse
```

This will create SonarQube projects with keys like:

* `NexLynkDMS_pl-nldms-main`
* `TEGManager_tegm`
* `HaemoCloud_lambda`

---

## Usage

Clone the repo and run the script (default `--workers` is 2; raise only if SonarQube and your build cache can handle more):

```bash
git clone https://github.com/<your-org>/sonar-project-loader.git
cd sonar-project-loader

python3 add_repos.py
```

The script will:

1. Create projects in SonarQube (if they don’t exist).
2. Clone each repository into `/tmp`.
3. Detect the build system.
4. Run the appropriate SonarQube scan command.
5. Print a **summary report** of created projects, existing projects, scans, and failures.

> Concurrency tip: default is `--workers 2`; raise gradually if SonarQube and your build cache can handle more parallel jobs.

---

## Sample Log Output

A full example run log is provided in [`example.log`](example.log).
Here’s a snippet:

```text
[INFO] Processing pl-nldms-main (NLD)
[OK] Project NLD_pl-nldms-main created successfully.
Cloning into '/tmp/pl-nldms-main'...
[INFO] Detected Maven project
[OK] Scan completed for NLD_pl-nldms-main

=== Summary ===
Projects created: 3
Projects already existed: 1
Repos scanned successfully: 3
Failures: 1

[INFO] All repos processed.
```

---

## Troubleshooting

* **Missing CLI tool**: Re-run `./setup_env.sh` to install missing dependencies.
* **Auth errors**: Check your `SONAR_TOKEN` and SonarQube user permissions.
* **Project not visible**: Verify the project key matches what’s in `repos.txt`.

---

## Notes

* Designed for **SonarQube Community Edition** (does not support high-level portfolio grouping).
* Uses **prefixes** as a lightweight way to organize projects.
* Extendable to support more languages or CI/CD integrations (e.g., GitHub Actions, Jenkins).

