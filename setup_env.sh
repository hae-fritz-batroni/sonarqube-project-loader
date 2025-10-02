#!/usr/bin/env bash
set -e

echo ">>> Setting up Python virtual environment..."

sudo apt-get update -y
sudo apt install -y python3.10-venv

# Ensure Python venv package is available
if ! dpkg -s python3-venv >/dev/null 2>&1; then
    echo ">>> Installing python3-venv..."
    sudo apt-get install -y python3-venv
fi

# Ensure unzip is available
if ! command -v unzip >/dev/null 2>&1; then
    echo ">>> Installing unzip..."
    sudo apt-get install -y unzip
fi

# Create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo ">>> Virtual environment created at .venv/"
fi

# Detect correct activation script
if [ -f ".venv/bin/activate" ]; then
    ACTIVATE=".venv/bin/activate"
else
    echo ">>> ERROR: Could not find activate script in .venv"
    exit 1
fi

# Activate venv
echo ">>> Activating virtual environment..."
source "$ACTIVATE"

# Upgrade pip + install requirements
pip install --upgrade pip
[ -f "requirements.txt" ] && pip install -r requirements.txt



# -------------------------------------------------------------
# Install Java (OpenJDK 17)
# -------------------------------------------------------------
if ! command -v java >/dev/null 2>&1; then
    echo ">>> Installing OpenJDK 17..."
    sudo apt-get update
    sudo apt-get install -y openjdk-17-jdk
else
    echo ">>> Java detected: $(java -version 2>&1 | head -n 1)"
fi

if [ -z "$JAVA_HOME" ] && command -v java >/dev/null 2>&1; then
    JAVA_PATH=$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")
    export JAVA_HOME="$JAVA_PATH"
    echo ">>> JAVA_HOME set to $JAVA_PATH"
    if ! grep -q "JAVA_HOME" ~/.bashrc 2>/dev/null; then
        echo "export JAVA_HOME=\"$JAVA_PATH\"" >> ~/.bashrc
        echo "export PATH=\"\$JAVA_HOME/bin:\$PATH\"" >> ~/.bashrc
    fi
fi

# -------------------------------------------------------------
# Install Maven
# -------------------------------------------------------------
if ! command -v mvn >/dev/null 2>&1; then
    echo ">>> Installing Maven..."
    sudo apt-get install -y maven
else
    echo ">>> Maven detected: $(mvn -v | head -n 1)"
fi

# -------------------------------------------------------------
# Install .NET SDK (7.0)
# -------------------------------------------------------------
if ! command -v dotnet >/dev/null 2>&1; then
    echo ">>> Installing .NET SDK 7.0..."
    wget https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/packages-microsoft-prod.deb -O packages-microsoft-prod.deb
    sudo dpkg -i packages-microsoft-prod.deb
    rm packages-microsoft-prod.deb
    sudo apt-get update
    sudo apt-get install -y dotnet-sdk-7.0
else
    echo ">>> .NET SDK detected: $(dotnet --version)"
fi

# -------------------------------------------------------------
# Install Go (1.22.x)
# -------------------------------------------------------------
if ! command -v go >/dev/null 2>&1; then
    echo ">>> Installing Go 1.22..."
    GO_VERSION=1.22.5
    wget https://go.dev/dl/go$GO_VERSION.linux-amd64.tar.gz -O /tmp/go.tar.gz
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf /tmp/go.tar.gz
    rm /tmp/go.tar.gz
    export PATH="/usr/local/go/bin:$PATH"
    if ! grep -q "/usr/local/go/bin" ~/.bashrc 2>/dev/null; then
        echo "export PATH=\"/usr/local/go/bin:\$PATH\"" >> ~/.bashrc
    fi
    go version
else
    echo ">>> Go detected: $(go version)"
fi

# -------------------------------------------------------------
# Install SonarScanner CLI
# -------------------------------------------------------------
if ! command -v sonar-scanner >/dev/null 2>&1; then
    echo ">>> Installing SonarScanner CLI..."
    SONAR_SCANNER_VERSION=5.0.1.3006
    wget https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-$SONAR_SCANNER_VERSION-linux.zip -O /tmp/sonar-scanner.zip
    sudo unzip -q /tmp/sonar-scanner.zip -d /opt
    rm /tmp/sonar-scanner.zip
    sudo ln -sf /opt/sonar-scanner-$SONAR_SCANNER_VERSION-linux/bin/sonar-scanner /usr/local/bin/sonar-scanner
    sonar-scanner --version
else
    echo ">>> SonarScanner detected: $(sonar-scanner --version | head -n 1)"
fi

# -------------------------------------------------------------
# Final checks
# -------------------------------------------------------------
echo ">>> Versions installed:"
java -version
mvn -v
dotnet --version
go version
sonar-scanner --version

echo ">>> Setup complete."
echo "Run the script with:"
echo "  source $ACTIVATE && python3 add_repos.py"
