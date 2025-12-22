from Courseplanner import get_dept_catalog, build_course_embeddings_index

if __name__ == "__main__":
    for dept in ["CMPSC", "CMPEN", "MATH", "STAT"]:
        catalog = get_dept_catalog(dept)
        print("Indexing", dept, "courses:", len(catalog))
        build_course_embeddings_index(catalog, dept)
    print("Done.")
