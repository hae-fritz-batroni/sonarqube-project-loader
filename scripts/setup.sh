#!/usr/bin/env bash
set -e
echo -e "\e[32m====================================================\e[0m"
echo -e "\e[32m       SonarQube Multi-Language Scanner Setup       \e[0m"
echo -e "\e[32m====================================================\e[0m"

sudo apt-get update -y

# -------------------------------------------
# Python + Virtual Env
# -------------------------------------------
echo -e "\e[34m>>> Setting up Python environment...\e[0m"
sudo apt install -y python3.10-venv python3-venv unzip

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo ">>> .venv created"
fi

source .venv/bin/activate
pip install --upgrade pip
[ -f "requirements.txt" ] && pip install -r requirements.txt
# Ensure test/coverage tooling for Python scans
pip install pytest coverage

# -------------------------------------------
# Java 17
# -------------------------------------------
if ! command -v java >/dev/null 2>&1; then
    echo -e "\e[34m>>> Installing OpenJDK 17...\e[0m"
    sudo apt-get install -y openjdk-17-jdk
fi
export JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$(which java)")")")
grep -q "JAVA_HOME" ~/.bashrc || echo "export JAVA_HOME=${JAVA_HOME}" >> ~/.bashrc
echo "JAVA_HOME=${JAVA_HOME}"


# -------------------------------------------
# Maven
# -------------------------------------------
echo -e "\e[34m>>> Checking Maven...\e[0m"
sudo apt-get install -y maven
# Pre-fetch JaCoCo plugin so Maven builds can attach coverage offline
mvn -q -DskipTests dependency:get -Dartifact=org.jacoco:jacoco-maven-plugin:0.8.11 || true


# -------------------------------------------
# .NET SDK + SonarScanner for .NET
# -------------------------------------------
echo -e "\e[34m>>> Installing .NET SDK + Scanner for .NET...\e[0m"
if ! command -v dotnet >/dev/null 2>&1; then
    wget https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/packages-microsoft-prod.deb -O packages-microsoft-prod.deb
    sudo dpkg -i packages-microsoft-prod.deb && rm packages-microsoft-prod.deb
    sudo apt-get update
    sudo apt-get install -y dotnet-sdk-8.0
fi

# Required for Linux MSBuild compatibility
sudo apt-get install -y nuget mono-complete build-essential

# Install dotnet Sonar Scanner (must be global)
dotnet tool update --global dotnet-sonarscanner || dotnet tool install --global dotnet-sonarscanner
# Coverlet for coverage collection
dotnet tool update --global coverlet.console || dotnet tool install --global coverlet.console

# Add to PATH permanently
grep -q ".dotnet/tools" ~/.bashrc || echo 'export PATH="$HOME/.dotnet/tools:$PATH"' >> ~/.bashrc
export PATH="$HOME/.dotnet/tools:$PATH"


# -------------------------------------------
# Go
# -------------------------------------------
if ! command -v go >/dev/null 2>&1; then
    echo -e "\e[34m>>> Installing Go 1.22...\e[0m"
    GO_VERSION=1.22.5
    wget https://go.dev/dl/go$GO_VERSION.linux-amd64.tar.gz -O /tmp/go.tar.gz
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf /tmp/go.tar.gz
    echo 'export PATH="/usr/local/go/bin:$PATH"' >> ~/.bashrc
    export PATH="/usr/local/go/bin:$PATH"
fi


# -------------------------------------------
# SonarScanner CLI (for Java/YAML/Generic)
# -------------------------------------------
if ! command -v sonar-scanner >/dev/null 2>&1; then
    echo -e "\e[34m>>> Installing SonarScanner CLI...\e[0m"
    VERSION=5.0.1.3006
    wget https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-$VERSION-linux.zip -O /tmp/sonar.zip
    sudo unzip -qo /tmp/sonar.zip -d /opt
    sudo ln -sf /opt/sonar-scanner-$VERSION-linux/bin/sonar-scanner /usr/local/bin/sonar-scanner
    rm /tmp/sonar.zip
fi

# -------------------------------------------
# Summary
# -------------------------------------------
echo -e "\e[32m>>> Installation Completed Successfully!\e[0m"
echo ""
echo "Java:       $(java -version 2>&1 | head -n 1)"
echo "Maven:      $(mvn -v | head -n 1)"
echo ".NET:       $(dotnet --version)"
echo "Go:         $(go version || echo 'Not Installed')"
echo "Scanner:    $(sonar-scanner --version | head -n 1)"
echo "DotnetScan: $(dotnet sonarscanner --version)"
echo "Coverlet:   $(coverlet --version 2>/dev/null || echo 'coverlet.console not installed')"
echo ""
echo -e "\e[33mNext Step: Run your repo loader script\e[0m"
echo "source .venv/bin/activate && python3 add_repos.py"
echo ""
