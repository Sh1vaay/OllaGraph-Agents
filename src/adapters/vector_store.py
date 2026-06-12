import os
import pandas as pd
import chromadb
from typing import Any, Optional

CHROMA_PATH = "db/chroma"

class ChromaManager:
    """Manages persistent ChromaDB vector storage and syncs entity records from GraphRAG parquet datasets."""

    def __init__(self, persist_path: str = CHROMA_PATH):
        self.persist_path = persist_path
        os.makedirs(self.persist_path, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_path)
        self.collection = self.client.get_or_create_collection(name="graphrag_entities")

    def sync_entities_from_parquet(self, parquet_path: str) -> bool:
        """Reads a final GraphRAG entities parquet file and syncs its contents into ChromaDB."""
        if not os.path.exists(parquet_path):
            print(f"Parquet file not found at {parquet_path}, skipping Chroma sync.")
            return False

        try:
            df = pd.read_parquet(parquet_path)
            
            # Ensure required columns are present
            required_cols = {"id", "name", "description", "description_embedding"}
            if not required_cols.issubset(df.columns):
                print(f"Parquet file at {parquet_path} is missing required columns. Found: {df.columns}")
                return False

            # Drop rows with missing descriptions or embeddings
            df = df.dropna(subset=["description", "description_embedding"])
            
            ids = []
            embeddings = []
            documents = []
            metadatas = []

            for _, row in df.iterrows():
                entity_id = str(row["id"])
                description_embedding = row["description_embedding"]
                
                # Check for embedding validity (must be list of floats)
                if isinstance(description_embedding, (list, tuple)) and len(description_embedding) > 0:
                    ids.append(entity_id)
                    embeddings.append(list(description_embedding))
                    documents.append(str(row["description"]))
                    metadatas.append({
                        "name": str(row["name"]),
                        "type": str(row.get("type", "Unknown"))
                    })

            if ids:
                # Add to collection in batches to prevent API size limit hits
                batch_size = 100
                for i in range(0, len(ids), batch_size):
                    self.collection.upsert(
                        ids=ids[i:i+batch_size],
                        embeddings=embeddings[i:i+batch_size],
                        documents=documents[i:i+batch_size],
                        metadatas=metadatas[i:i+batch_size]
                    )
                print(f"Successfully synced {len(ids)} entities to ChromaDB.")
                return True
            else:
                print("No valid entities found to sync.")
                return False

        except Exception as e:
            print(f"Error syncing parquet data to ChromaDB: {e}")
            return False

    def query_entities(self, query_embedding: list[float], top_k: int = 10) -> list[dict[str, Any]]:
        """Queries the ChromaDB collection using a raw query embedding vector."""
        try:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k
            )

            formatted_results = []
            if results and results.get("ids") and results["ids"][0]:
                for idx in range(len(results["ids"][0])):
                    formatted_results.append({
                        "id": results["ids"][0][idx],
                        "name": results["metadatas"][0][idx]["name"],
                        "type": results["metadatas"][0][idx]["type"],
                        "description": results["documents"][0][idx],
                        "distance": results["distances"][0][idx] if results.get("distances") else 0.0
                    })
            return formatted_results
        except Exception as e:
            print(f"Error querying ChromaDB: {e}")
            return []
            
    def get_latest_output_parquet(self, root_dir: str = ".") -> Optional[str]:
        """Finds the latest GraphRAG output folder and returns the entities parquet path."""
        output_base = os.path.join(root_dir, "output")
        if not os.path.isdir(output_base):
            return None
        subdirs = [
            os.path.join(output_base, d)
            for d in os.listdir(output_base)
            if os.path.isdir(os.path.join(output_base, d))
        ]
        if not subdirs:
            return None
        
        # Sort subdirs by modification time to find the newest run
        subdirs.sort(key=os.path.getmtime, reverse=True)
        latest_dir = subdirs[0]
        parquet_path = os.path.join(latest_dir, "artifacts", "create_final_entities.parquet")
        
        return parquet_path if os.path.exists(parquet_path) else None

    def export_graph_to_json(self, root_dir: str = ".") -> bool:
        """Reads final nodes and relationships parquets and exports them as a combined JSON for the 3D visualizer."""
        try:
            output_base = os.path.join(root_dir, "output")
            if not os.path.isdir(output_base):
                return False
            subdirs = [
                os.path.join(output_base, d)
                for d in os.listdir(output_base)
                if os.path.isdir(os.path.join(output_base, d))
            ]
            if not subdirs:
                return False
            subdirs.sort(key=os.path.getmtime, reverse=True)
            latest_dir = subdirs[0]
            
            nodes_path = os.path.join(latest_dir, "artifacts", "create_final_nodes.parquet")
            rel_path = os.path.join(latest_dir, "artifacts", "create_final_relationships.parquet")
            
            if not (os.path.exists(nodes_path) and os.path.exists(rel_path)):
                print("Nodes or relationships parquet not found, skipping graph export.")
                return False
                
            nodes_df = pd.read_parquet(nodes_path)
            rel_df = pd.read_parquet(rel_path)
            
            # Prepare nodes list
            nodes_list = []
            for _, row in nodes_df.iterrows():
                nodes_list.append({
                    "id": str(row["title"]),
                    "label": str(row["title"]),
                    "type": str(row.get("type", "Entity")),
                    "description": str(row.get("description", "")),
                    "val": int(row.get("degree", 1))
                })
                
            # Prepare links list
            links_list = []
            for _, row in rel_df.iterrows():
                links_list.append({
                    "source": str(row["source"]),
                    "target": str(row["target"]),
                    "description": str(row.get("description", "")),
                    "weight": float(row.get("weight", 1.0))
                })
                
            import json
            graph_data = {
                "nodes": nodes_list,
                "links": links_list
            }
            
            public_dir = os.path.join(root_dir, "public")
            os.makedirs(public_dir, exist_ok=True)
            json_path = os.path.join(public_dir, "graph_data.json")
            
            with open(json_path, "w") as f:
                json.dump(graph_data, f, indent=2)
                
            print(f"Successfully exported {len(nodes_list)} nodes and {len(links_list)} relationships to {json_path}")
            return True
            
        except Exception as e:
            print(f"Error exporting graph data to JSON: {e}")
            return False
