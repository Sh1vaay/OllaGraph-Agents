# OllaGraph-Agents

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AutoGen Version](https://img.shields.io/badge/AutoGen-0.13.0%2B-green.svg)](https://github.com/microsoft/autogen)
[![GraphRAG Version](https://img.shields.io/badge/GraphRAG-0.3.0%2B-orange.svg)](https://github.com/microsoft/graphrag)
[![Ollama Version](https://img.shields.io/badge/Ollama-0.4.0%2B-lightgrey.svg)](https://ollama.com/)

OllaGraph-Agents is a private, secure, and fully offline Retrieval-Augmented Generation (RAG) system. It builds a semantic knowledge graph from local documents using **Microsoft GraphRAG**, orchestrates multi-agent group discussions using **AG2 (AutoGen)**, manages persistent session state and hashes using **SQLite**, indexes entity descriptions locally in **ChromaDB**, runs all LLM inference via **Ollama**, and serves a gorgeous macOS Glassmorphism web client built on **Chainlit** including a **3D WebGL Force-Directed Graph Visualizer**.

---

## 📖 Table of Contents
* [System Architecture](#-system-architecture)
* [Application Flow](#-application-flow)
* [Key Features](#-key-features)
* [Quick Start (Local Setup)](#-quick-start-local-setup)
* [Configuration Guide](#-configuration-guide)
* [Developer Experience](#-developer-experience)
* [Security & Privacy Audit](#-security--privacy-audit)
* [Performance & Optimization](#-performance--optimization)
* [License](#-license)

---

## 🏗️ System Architecture

The following diagram illustrates the relationship between the front-end interface, local database modules, agent executors, and the Ollama service:

```mermaid
graph TD
    User[User] -->|Interacts| UI[Chainlit Web UI]
    UI -->|Session Queries| DB[SQLite DB - Memory/WAL]
    UI -->|Drag & Drop Docs| Index[Incremental Indexing Engine]
    Index -->|Checksum Bypass| HashCheck{MD5 Hash Match?}
    HashCheck -->|No| Marker[Marker PDF-to-MD]
    Marker -->|Runs GraphRAG| GraphRAG[GraphRAG Indexer]
    GraphRAG -->|Parquet Output| Sync[Vector & Graph Sync]
    Sync -->|Sync Vectors| Chroma[ChromaDB Vector Store]
    Sync -->|Export Nodes| GraphData[graph_data.json]
    
    UI -->|Open 3D Visualizer| WebGL[3D WebGL Graph Visualizer]
    GraphData -->|Loads Structure| WebGL
    
    UI -->|Sends Message| Orchestrator[Multi-Model Intent Router]
    Orchestrator -->|Conversational Intent| Chatter[Conversational Chatter Agent]
    Orchestrator -->|factual/Search Intent| Retriever[GraphRAG Retriever Agent]
    
    Retriever -->|Similarity Query| Chroma
    Retriever -->|Local/Global Search| GraphRAGSearch[GraphRAG Search Runner]
    
    Chatter -->|Fast LLM| OllamaChat[Ollama: gemma/phi3/qwen]
    Retriever -->|Heavy LLM| OllamaReason[Ollama: llama3/mistral]
```

---

## 🔄 Application Flow

The sequence of operations when initiating a user session and routing queries is described below:

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as "Chainlit Web UI"
    participant Router as "Intent Router"
    participant Agent as "AutoGen Agent Group"
    participant Chroma as "ChromaDB Store"
    participant RAG as "GraphRAG Search"
    participant Ollama as "Ollama Local Service"

    User->>UI: Launch Application
    UI->>UI: Check SQLite session history
    alt Session History Exists
        UI-->>User: Prompt "Resume Last Chat" or "Start New Chat"
    else New Chat Selected
        UI-->>User: Open Drag & Drop file uploader
        User->>UI: Upload document files
        UI->>UI: Calculate MD5 file hashes
        alt Hashes Match Stored DB
            UI-->>User: Skip indexing (Instant Ready)
        else Hashes Changed / New Files
            UI->>UI: Convert PDFs to Markdown & build GraphRAG Index
            UI->>Chroma: Sync Entity Embeddings from Parquet
            UI->>UI: Export graph_data.json
        end
    end
    
    User->>UI: Send search query / chat message
    UI->>Router: Detect Query Intent
    Router->>Ollama: Classify prompt as SEARCH or CONVERSATIONAL
    Ollama-->>Router: Classification tag
    
    alt Conversational Chit-Chat
        Router->>Agent: Route to Chatter Agent (Fast LLM config)
        Agent->>Ollama: Generate small talk response
        Ollama-->>Agent: Return response text
    else Search factual query
        Router->>Agent: Route to Retriever Agent (Heavy LLM config)
        Agent->>Chroma: Run similarity query on query embedding
        Chroma-->>Agent: Display matched entity names & summaries in UI
        Agent->>RAG: Invoke query_graphRAG tool
        RAG->>Ollama: Synthesize community context or local entities
        Ollama-->>RAG: Return synthesized response
        RAG-->>Agent: Return context text
    end
    Agent-->>UI: Stream response message
    UI->>UI: Log turn asynchronously to SQLite DB
    UI-->>User: Display final response
```

---

## 🌟 Key Features

1. **Persistent SQLite Memory (WAL Mode):**
   * Persists message logs and session tables using asynchronous SQLite (`aiosqlite`).
   * Configures Write-Ahead Logging (WAL) and 5000ms busy timeout properties, preventing write contentions and locking conditions.
   * Action buttons allow users to seamlessly **Resume Last Chat** or **Start New Workspace Sessions** on startup.
2. **Multi-Model Orchestration:**
   * Discovers local Ollama models dynamically on launch, assigning fast models (`gemma/phi3/qwen`) to conversational queries and heavy models (`llama3/mistral`) to deep context searches.
   * Employs intent-based routing to bypass costly GraphRAG search routines when user inputs are simple greetings or pequeña chat.
3. **Local Persistent Vector Store (ChromaDB):**
   * Embeds entity metadata and names into a persistent local collection (`db/chroma`).
   * Renders the top matched nodes inside the chat window before invoking GraphRAG search queries, showing users what references were found in the vector index.
4. **Smart Incremental Indexing Engine:**
   * Calculates MD5 hashes of uploaded documents and stores them in SQLite (`indexed_files`).
   * Bypasses the heavy PDF-to-Markdown parser (Marker) and GraphRAG index creation if file hashes are unchanged, keeping setup instant.
5. **Interactive 3D WebGL Graph Visualizer:**
   * Reads node and relationship parquets and exports coordinates, type tags, and link connections to `public/graph_data.json`.
   * Serves an interactive 3D WebGL page from `/public/graph_visualizer.html` featuring:
     - **Legend Groups:** Filter and highlight nodes by entity type.
     - **Spotlight Search:** Instantly filters matching entities and dims unrelated nodes.
     - **Camera Navigation:** Click nodes to center the WebGL camera smoothly.
     - **Detail Cards:** Displays complete entity details and connection weights on hover.

---

## 🚀 Quick Start (Local Setup)

### Prerequisites
* **Python:** Version 3.10 or higher.
* **Ollama:** Installed and running locally on port `11434`.

### 1. Pull Local Models
Verify you have pulled the required LLM and embedding models in Ollama:
```bash
# Pull the default Reasoning LLM
ollama pull llama3

# Pull the default Embedding Model
ollama pull nomic-embed-text
```

### 2. Install Dependencies
We recommend utilizing `uv` for fast package downloads:
```bash
# Create virtual environment
uv venv

# Activate virtual environment
source .venv/bin/activate

# Install requirements
uv pip install -r requirements.txt
```

### 3. Launch the Project
Run the startup script. It automatically verifies model presence, patches the GraphRAG package with Ollama-compatible adapters, and starts the UI:
```bash
chmod +x run_project.sh
./run_project.sh
```

---

## ⚙️ Configuration Guide

### Custom GraphRAG settings (`settings.yaml`)
Configurations are defined inside your root `settings.yaml`. A default template is saved inside `templates/settings.yaml`.
* **llm.type / embeddings.llm.type:** Configured as `openai_chat` and `openai_embedding` respectively.
* **api_base:** Configured to point to Ollama's local endpoints (`http://localhost:11434/v1` and `http://localhost:11434/api`).

### Offline Mock Interfaces
If you wish to test or preview the user interface styling locally without launching the Python server or downloading models, open these files directly in your browser:
* **Mock Chat UI:** [public/mock_chat_ui.html](file:///home/alpha-square/Videos/Autogen_GraphRAG_Ollama-main/public/mock_chat_ui.html)
* **Mock 3D Graph Visualizer:** [public/mock_graph_visualizer.html](file:///home/alpha-square/Videos/Autogen_GraphRAG_Ollama-main/public/mock_graph_visualizer.html)

---

## 🛠️ Developer Experience

### Local Linting & Formatting
To keep code clean and maintain separation of concerns:
```bash
# Install toolchains
pip install black flake8 mypy

# Check formatting
black --check src/ app.py

# Lint checks
flake8 src/ app.py
```

### Contribution Workflow
1. Fork the project repository.
2. Create a development feature branch: `git checkout -b feature/my-cool-feature`.
3. Test changes locally using `run_project.sh` and offline mockup files.
4. Ensure files are linted, then open a Pull Request.

---

## 🛡️ Security & Privacy Audit

* **100% Data Privacy:** Zero cloud connections. All prompts, indexing steps, and vector search operations are executed on your local device.
* **Port Bindings:** The Chainlit development web server binds explicitly to localhost `127.0.0.1`, shielding active processes from network sniffers.
* **Sensitive Exclusions:** `.gitignore` blocks source materials (`input/`), parquet outputs (`output/`), vector directories (`db/`), and configuration files (`settings.yaml`) from being pushed to public remotes.
* **Credentials Management:** Relies on env string interpolation (`${GRAPHRAG_API_KEY}`) to load keys dynamically when cloud options are chosen.

---

## ⚡ Performance & Optimization

* **Write-Ahead Logging (WAL):** Prevents database write blockages by writing message histories in parallel logs.
* **Parallel Processing:** Uses PyTorch multi-process spawning for Marker layout conversions, matching VRAM constraints automatically.
* **Non-Blocking Network Calls:** Interrogates Ollama using asynchronous client requests (`AsyncClient`), avoiding event loop starvation.

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
