import os
import re
import sqlite3
import pandas as pd

DB_PATH = "db/structured_data.db"

def clean_table_name(name: str) -> str:
    """Cleans a filename to be a safe SQLite table name."""
    # Remove file extensions
    name = re.sub(r"\.(csv|xlsx|xls)$", "", name, flags=re.IGNORECASE)
    # Replace invalid chars with underscores
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    # Ensure it starts with a letter or underscore
    if name and name[0].isdigit():
        name = "_" + name
    return name.lower()

def clean_column_name(col: str) -> str:
    """Cleans a column header to be a safe, clean SQL column name."""
    col = str(col).strip()
    col = re.sub(r"[^a-zA-Z0-9_]", "_", col)
    col = re.sub(r"_+", "_", col) # deduplicate underscores
    if col and col[0].isdigit():
        col = "_" + col
    return col.lower()

def ingest_table_file(file_path: str, file_name: str) -> str:
    """Ingests a CSV or Excel file into the SQLite database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    table_name = clean_table_name(file_name)
    
    # Read using pandas based on file type
    if file_name.lower().endswith(".csv"):
        df = pd.read_csv(file_path)
    elif file_name.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(file_path)
    else:
        raise ValueError("Unsupported file type. Only CSV and Excel files are supported.")

    # Clean columns
    df.columns = [clean_column_name(c) for c in df.columns]
    
    # Write to SQLite
    with sqlite3.connect(DB_PATH) as conn:
        df.to_sql(table_name, conn, if_exists="replace", index=False)
        
    return table_name

def get_db_schema() -> str:
    """Queries sqlite_master to get schema of all active tables."""
    if not os.path.exists(DB_PATH):
        return "No structured database tables have been uploaded yet."
        
    schema_info = []
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        if not tables:
            return "No tables exist in the structured database."
            
        for (table_name,) in tables:
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = cursor.fetchall()
            col_desc = [f"{col[1]} ({col[2]})" for col in columns]
            schema_info.append(f"Table: `{table_name}`\nColumns: {', '.join(col_desc)}")
            
    return "\n\n".join(schema_info)

def df_to_markdown(df) -> str:
    """Converts a pandas DataFrame into a markdown table manually (no tabulate dependency required)."""
    if df.empty:
        return "Empty result set."
        
    headers = list(df.columns)
    # Header row
    markdown_lines = ["| " + " | ".join(headers) + " |"]
    # Divider row
    markdown_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    
    # Data rows
    for _, row in df.iterrows():
        row_str = "| " + " | ".join(str(val).replace("\n", " ").replace("|", "\\|") for val in row) + " |"
        markdown_lines.append(row_str)
        
    return "\n".join(markdown_lines)

def execute_sql_query(query: str) -> str:
    """Executes a SQL query against the structured database and returns a markdown table or error message."""
    if not os.path.exists(DB_PATH):
        return "Error: Structured database does not exist. Please upload CSV or Excel files first."
        
    # Security check: Read-only check
    clean_query = query.strip().upper()
    forbidden_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE"]
    for keyword in forbidden_keywords:
        if re.search(r"\b" + keyword + r"\b", clean_query):
            return f"Error: SQL execution rejected. Only read-only SELECT queries are allowed."

    try:
        with sqlite3.connect(DB_PATH) as conn:
            # Load into DataFrame to easily enforce limits and render markdown
            df = pd.read_sql_query(query, conn)
            
            # Enforce output row limit to prevent context window overflow
            row_count = len(df)
            if row_count > 50:
                df = df.head(50)
                limit_msg = f"\n\n*(Showing top 50 of {row_count} total rows)*"
            else:
                limit_msg = ""
                
            return df_to_markdown(df) + limit_msg
            
    except Exception as e:
        return f"SQL Execution Error: {str(e)}"
