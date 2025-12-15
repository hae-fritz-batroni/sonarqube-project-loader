#!/usr/bin/env bash
set -e

echo "===================================================="
echo "       SonarQube Multi-Language Scanner Setup"
echo "===================================================="

# Ensure Homebrew exists
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found. Installing..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# -------------------------------------------
# Python + Virtual Environment
# -------------------------------------------
echo ">>> Setting up Python environment..."
brew install python3 unzip >/dev/null

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo ">>> .venv created"
fi

source .venv/bin/activate
pip install --upgrade pip >/dev/null
[ -f "requirements.txt" ] && pip install -r requirements.txt
# Ensure test/coverage tooling for Python scans
pip install pytest coverage >/dev/null

# -------------------------------------------
# Java 17
# -------------------------------------------
echo ">>> Checking Java..."
if ! command -v java >/dev/null 2>&1; then
    echo "Installing OpenJDK 17..."
    brew install openjdk@17
    sudo ln -sfn /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk /Library/Java/JavaVirtualMachines/openjdk-17.jdk
fi

export JAVA_HOME=$(/usr/libexec/java_home -v 17)
grep -q "JAVA_HOME" ~/.zshrc || echo "export JAVA_HOME=\"$JAVA_HOME\"" >> ~/.zshrc

# -------------------------------------------
# Maven
# -------------------------------------------
echo ">>> Installing Maven..."
brew install maven
# Pre-fetch JaCoCo plugin so Maven builds can attach coverage offline
mvn -q -DskipTests dependency:get -Dartifact=org.jacoco:jacoco-maven-plugin:0.8.11 || true

# -------------------------------------------
# .NET + SonarScanner for .NET
# -------------------------------------------
echo ">>> Installing .NET SDK + SonarScanner..."
if ! command -v dotnet >/dev/null 2>&1; then
    brew install --cask dotnet-sdk
fi

dotnet tool update --global dotnet-sonarscanner || dotnet tool install --global dotnet-sonarscanner
# Coverlet for coverage collection
dotnet tool update --global coverlet.console || dotnet tool install --global coverlet.console

grep -q ".dotnet/tools" ~/.zshrc || echo 'export PATH="$HOME/.dotnet/tools:$PATH"' >> ~/.zshrc
export PATH="$HOME/.dotnet/tools:$PATH"

# -------------------------------------------
# Go
# -------------------------------------------
echo ">>> Installing Go..."
if ! command -v go >/dev/null 2>&1; then
    brew install go
fi

grep -q "/usr/local/go/bin" ~/.zshrc || echo 'export PATH="/usr/local/go/bin:$PATH"' >> ~/.zshrc

# -------------------------------------------
# SonarScanner CLI (Generic Scanner)
# -------------------------------------------
if ! command -v sonar-scanner >/dev/null 2>&1; then
    echo ">>> Installing SonarScanner locally..."
    brew install sonar-scanner

    export PATH="$HOME/.sonar-scanner/sonar-scanner-$VERSION-macosx/bin:$PATH"
fi


# -------------------------------------------
# Summary
# -------------------------------------------
echo ""
echo "===================================================="
echo ">>> Installation Completed Successfully!"
echo "===================================================="
echo "Java:       $(java -version 2>&1 | head -n 1)"
echo "Maven:      $(mvn -v | head -n 1)"
echo ".NET:       $(dotnet --version)"
echo "Go:         $(go version)"
echo "Scanner:    $(sonar-scanner --version | head -n 1)"
echo "DotnetScan: $(dotnet sonarscanner --version)"
echo "Coverlet:   $(coverlet --version 2>/dev/null || echo 'coverlet.console not installed')"
echo ""
echo "Next: Run your repo loader"
echo "   source .venv/bin/activate"
echo "   python3 add_repos.py"
echo ""
