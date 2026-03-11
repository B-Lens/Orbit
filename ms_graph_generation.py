import kuzu
import networkx as nx
import json

DB_PATH = "src/orbit/market_intelligence/.gitnexus/kuzu"
OUTPUT_FILE = "ms_graph.json"

db = kuzu.Database(DB_PATH)
conn = kuzu.Connection(db)


# -------------------------------------------------
# 1️⃣ Get node and relationship tables
# -------------------------------------------------

def get_tables():
    result = conn.execute("CALL show_tables() RETURN *;").get_all()

    node_tables = [r[1] for r in result if r[2] == "NODE"]
    rel_tables = [r[1] for r in result if r[2] == "REL"]

    return node_tables, rel_tables


# -------------------------------------------------
# 2️⃣ Get columns for table
# -------------------------------------------------

def get_columns(table_name):
    info = conn.execute(
        f"CALL table_info('{table_name}') RETURN *;"
    ).get_all()

    return [row[1] for row in info]


# -------------------------------------------------
# 3️⃣ Build NetworkX graph
# -------------------------------------------------

def clean_props(props):
    cleaned = {}
    for k, v in props.items():
        if isinstance(v, (dict, list)):
            cleaned[k] = json.dumps(v)
        else:
            cleaned[k] = v
    return cleaned

def build_networkx_graph():
    G = nx.DiGraph()

    node_tables, rel_tables = get_tables()

    # -----------------------------
    # Add nodes
    # -----------------------------
    for label in node_tables:
        if label not in ["Class", "Function", "Process"]:
            continue
        print(f"Processing node table: {label}")

        cols = get_columns(label)
        if not cols:
            continue

        col_string = ", ".join([f"n.{c}" for c in cols])

        query = f"""
        MATCH (n:`{label}`)
        RETURN id(n), {col_string}
        """

        try:
            result = conn.execute(query)

            while result.has_next():
                row = result.get_next()
                internal_id = str(row[0])
                props = dict(zip(cols, row[1:]))

                props = clean_props(props)

                # Use internal ID as unique node id
                G.add_node(
                    internal_id,
                    __meta__={
                        "table": label
                    },   # renamed to avoid collision
                    **props
                )
                print(f"added node")

        except Exception as e:
            print(f"Error processing node table {label}: {e}")

    # -----------------------------
    # Add edges
    # -----------------------------
    FLOW_RELATIONS = [
        "CALLS",
        "USES",
        "ENTRY_POINT",
        "TERMINAL",
        "DEPENDS_ON",
        "CodeRelation"
    ]
    for rel in rel_tables:
        # if rel not in FLOW_RELATIONS:
        #     continue
        print(f"Processing relationship table: {rel}")

        cols = get_columns(rel)
        col_string = ""
        if cols:
            col_string = ", " + ", ".join([f"r.{c}" for c in cols])

        query = f"""
        MATCH (a)-[r:`{rel}`]->(b)
        RETURN id(a), id(b){col_string}
        """

        try:
            result = conn.execute(query)

            while result.has_next():
                row = result.get_next()

                from_id = str(row[0])
                to_id = str(row[1])
                props = dict(zip(cols, row[2:]))

                props = clean_props(props)

                G.add_edge(
                    from_id,
                    to_id,
                    __meta__={
                        "table": rel
                    },
                    **props
                )

        except Exception as e:
            print(f"Error processing rel table {rel}: {e}")

    return G


# -------------------------------------------------
# 4️⃣ Export
# -------------------------------------------------

def export_to_json(G):
    data = nx.node_link_data(G)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Exported {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")


# -------------------------------------------------
# 5️⃣ Run
# -------------------------------------------------

if __name__ == "__main__":
    G = build_networkx_graph()
    export_to_json(G)