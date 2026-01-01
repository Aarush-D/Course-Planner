# build_index.py
from Courseplanner import get_dept_catalog, build_local_embeddings_index

if __name__ == "__main__":
    dept = "CMPSC"  # change as needed
    catalog = get_dept_catalog(dept)
    build_local_embeddings_index(catalog, dept)
    print(f"Built index for {dept}")