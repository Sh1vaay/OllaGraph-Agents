#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status,
# if an undefined variable is referenced, or if any command in a pipeline fails.
set -euo pipefail

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORKSPACE_DIR"

echo "=========================================================="
echo " Starting OllaGraph-Agents (June 2026)"
echo "=========================================================="

# 1. Check if Ollama is running
echo "[+] Checking local Ollama service..."
TAGS_RESPONSE=$(curl -s http://localhost:11434/api/tags || echo "")
if [ -z "$TAGS_RESPONSE" ]; then
    echo "[!] Error: Ollama service is not running or accessible at http://localhost:11434."
    echo "    Please start the Ollama server ('ollama serve') in another window."
    exit 1
fi
echo "[+] Ollama service is active and responsive."

# Verify if target models are pulled
for model in "llama3" "nomic-embed-text"; do
    # Match model name with or without tag suffix (e.g. llama3:latest or llama3)
    if ! echo "$TAGS_RESPONSE" | grep -E -q "\"name\":\"${model}(:|\")"; then
        echo "[!] Warning: Model '${model}' was not found in your local Ollama list."
        echo "[+] Attempting to pull '${model}' automatically..."
        if ! ollama pull "${model}"; then
            echo "[!] Failed to pull '${model}'. Please run 'ollama pull ${model}' manually."
        fi
    else
        echo "[+] Model '${model}' verified."
    fi
done

# 2. Check for uv package manager
if ! command -v uv &> /dev/null; then
    echo "[!] 'uv' package manager was not found in PATH."
    echo "    Installing uv is recommended. You can install it using: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "    Falling back to standard python venv and pip..."
    USE_UV=false
else
    USE_UV=true
fi

# 3. Setup and activate virtual environment
if [ "$USE_UV" = true ]; then
    if [ ! -d ".venv" ]; then
        echo "[+] Creating virtual environment using uv..."
        uv venv
    fi
    source .venv/bin/activate
    echo "[+] Syncing dependencies with uv..."
    uv pip install -r requirements.txt
else
    if [ ! -d "venv" ]; then
        echo "[+] Creating virtual environment using standard python venv..."
        python3 -m venv venv
    fi
    source venv/bin/activate
    echo "[+] Syncing dependencies with pip..."
    pip install -r requirements.txt
fi

# 4. Initialize GraphRAG files if missing
if [ ! -f "settings.yaml" ]; then
    echo "[+] Initializing GraphRAG structure..."
    mkdir -p ./input/markdown
    python3 -m graphrag.index --init --root .
    if [ -f "templates/settings.yaml" ]; then
        cp templates/settings.yaml ./
        echo "[+] Configured custom Ollama settings.yaml in root."
    fi
fi

# 5. Apply Ollama compatibility patches to GraphRAG package
echo "[+] Applying GraphRAG library adapters..."
GRAPHRAG_PATH=$(python3 -c "import graphrag, os; print(os.path.dirname(graphrag.__file__))" 2>/dev/null || echo "")

if [ -n "$GRAPHRAG_PATH" ]; then
    # Patch openai_embeddings_llm.py
    if [ -f "src/adapters/openai_embeddings_llm.py" ]; then
        TARGET="$GRAPHRAG_PATH/llm/openai/openai_embeddings_llm.py"
        if ! cmp -s "src/adapters/openai_embeddings_llm.py" "$TARGET"; then
            cp "src/adapters/openai_embeddings_llm.py" "$TARGET"
            echo "[+] Patched: $TARGET"
        fi
    fi
    # Patch embedding.py
    if [ -f "src/adapters/embedding.py" ]; then
        TARGET="$GRAPHRAG_PATH/query/llm/oai/embedding.py"
        if ! cmp -s "src/adapters/embedding.py" "$TARGET"; then
            cp "src/adapters/embedding.py" "$TARGET"
            echo "[+] Patched: $TARGET"
        fi
    fi
else
    echo "[!] Warning: GraphRAG library not found in Python path. Adapters could not be applied automatically."
fi

# 6. Verify documents index status
MD_COUNT=$(find input/markdown -name "*.md" 2>/dev/null | wc -l || echo 0)
if [ "$MD_COUNT" -eq 0 ]; then
    echo "[!] Warning: No markdown files found in 'input/markdown'."
    echo "    Please add your document source files to 'input/markdown' to query them."
else
    if [ ! -d "output" ]; then
        echo "[+] Found $MD_COUNT source files. Building GraphRAG database index (this might take a few minutes)..."
        python3 -m graphrag.index --root .
    else
        echo "[+] Found existing indexed database in 'output/'. Skipping indexing."
        echo "    If you added new files, run: python3 -m graphrag.index --root ."
    fi
fi

# 7. Start Chainlit UI
echo "[+] Launching Chainlit Web UI (restricted to localhost for security)..."
chainlit run app.py --host 127.0.0.1 --port 8000

