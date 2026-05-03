# 🧠 DB Studio — AI Database Platform

Design, modify, and query databases using natural language.

DB Studio is an agent-based platform that enables users to:

- 🏗️ Create databases from requirements  
- ✏️ Modify schemas safely  
- 💬 Query data using natural language (NL → SQL)  

Built with LLM agents, validation pipelines, and cloud-backed storage.

---

# 🚀 Overview

DB Studio is a modular AI-powered platform that allows users to interact with databases without writing SQL manually.

It combines:

- Natural language processing  
- Agent-based workflows  
- Structured validation pipelines  
- Cloud persistence  

The system is designed for:

- Rapid database prototyping  
- Safe schema evolution  
- Intelligent querying with explainability  

---

# ✨ Features

### 🏗️ Feature 1 — Database Creation
- Convert business requirements → database schema  
- Generate tables and relationships  
- Produce SQLite database files  
- Generate schema documentation / ERD  

---

### ✏️ Feature 2 — Database Modification
- Modify existing schemas safely  
- Validate schema changes before applying  
- Approval workflow before execution  
- Preserve database integrity  

---

### 💬 Feature 3 — Natural Language Querying
- Natural Language → SQL generation  
- Query validation pipeline  
- Human approval workflow  
- SQL execution  
- Natural language response generation  
- Query traceability/debugging  

---

# 🏗️ Architecture

The project follows a feature-based modular architecture:

```bash 

app.py
feature1_app.py
feature2_app.py
feature3_app.py

Features/
│
├── Feature1_create_db/
├── Feature2_modify_db/
├── Feature3_chat_db/
│
shared/
├── config.py
├── blob_storage.py
├── workspace.py

```

# 🔄 System Flow
```text

User Input (UI)
      ↓
Workspace State Update
      ↓
Agent Pipeline
      ↓
SQL Generation
      ↓
Validation / Approval
      ↓
Execution (SQLite)
      ↓
Results + NL Response
      ↓
Persist to Blob Storage
```

# ⚙️ Tech Stack
Python
Streamlit
Azure OpenAI
Azure Blob Storage
SQLite
Pydantic
LangGraph / Agent workflows
SQL validation pipelines


# ⚡ Setup

## 1) Clone repository
```bash

git clone https://github.com/your-username/DB-Studio.git
cd DB-Studio
```

## 2) Create virtual environment

``` bash
python3 -m venv .venv
source .venv/bin/activate
```

For Windows:

```bash
.venv\Scripts\activate
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure environment variables

Copy the example environment file:

```bash
cp .env.example .env
```

Then open `.env` and fill in your credentials:

- Azure OpenAI credentials  
- Azure Blob Storage credentials  
- deployment names  
- required environment variables

## 5. Run the application

```bash
streamlit run app.py
```

---

# 💬 Usage Example

### Input

```text
Show top selling products
```

### Pipeline

```text
NL Query
→ SQL Generation
→ Validation
→ Execution
→ Response
```

### Output

- Generated SQL query  
- Query results table  
- Natural language explanation  

---

# 🗂️ Project Structure

```bash
DB-Studio/
├── app.py
├── feature1_app.py
├── feature2_app.py
├── feature3_app.py

├── Features/
│   ├── Feature1_create_db/
│   ├── Feature2_modify_db/
│   └── Feature3_chat_db/

├── shared/
│   ├── config.py
│   ├── blob_storage.py
│   └── workspace.py

├── requirements.txt
└── README.md
```

---

# 🔐 Security Notes

- Never commit `.env`
- Never expose API keys
- Use secret managers in production
- Secure database credentials

---

# 🧪 Testing

- Unit tests for agent nodes  
- Blob storage mocking  
- Integration testing for NL → SQL flow  

---

# 🚀 Deployment

- Dockerize the application  
- Use HTTPS proxy  
- Inject secrets via CI/CD  
- Monitor logs  

---
