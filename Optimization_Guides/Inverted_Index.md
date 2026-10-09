# 🗃️ Inverted Index: Optimization Guide

When an object is added or updated, Weaviate's BM25 algorithm updates its internal statistics (term frequencies, object frequencies, and object lengths) for that object only. This incremental update keeps keyword searches accurate without recalculating the entire model.

> **Note:** In standard BM25 terminology, an 'object' is referred to as a 'document'.

## 🔧 How **Inverted Index is created**

Weaviate uses a modular architecture where **every property and every enabled index type results in a separate physical data structure.**

> Example: If you have a property named 'Name' and you enable both indexSearchable and indexFilterable, Weaviate creates:
>
> 1. **Index A (Searchable):** An index optimized for BM25 ranking (storing term frequencies and property lengths per object).
> 2. **Index B (Filterable):** A Roaring Bitmap optimized for rapid "yes/no" inclusion logic.

**The Developer Impact:** Enabling indexes you don't use doesn't just "sit there": it doubles the computational work during imports (writes) and increases the memory/disk footprint. **Only enable what you plan to query.**

---

## 🚦 Inverted Index Types

Configure these within the `Property` definition of your schema.

| **Index Type** | **Function** | **Applicable Types** | **Default** |
| --- | --- | --- | --- |
| **`indexSearchable`** | Powers **BM25** and **Hybrid** search. | `text`, `text[]` | **True** |
| **`indexFilterable`** | Powers **Where** filters (Equality/Inequality). | All types except `blob`, `geoCoordinates`, `object` and `phoneNumber` data types including arrays thereof | **True** |
| **`indexRangeFilters`** | Powers **numerical/date ranges** (e.g., `>`, `<`). | `int`, `number` and `date` (not arrays) | **False** |

### Comparison: Which index handles the operator?

If you enable both Filterable and Range on a number, Weaviate routes the query:

- **`Equal` / `NotEqual`**: Handled by `indexFilterable`. If only `indexRangeFilters` is enabled, it handles these too.
- **`Greater` / `Less Than`**: Handled by `indexRangeFilters`.

> **Tip:** For `int`, `number` or `date` properties, `indexRangeFilters` alone covers both equality and range queries. You can set `indexFilterable=False` to save one index.

---

### 🔍 Searchable vs. Filterable

- **Searchable (`indexSearchable`):** This is for **Keyword/BM25 Search**. It allows you to search for individual **words inside** a property. Weaviate tokenizes the text (e.g., "The quick brown fox") so you can find the object by searching for "quick".
- **Filterable (`indexFilterable`):** This is for **Metadata Filtering**. It allows you to use the property to **restrict your results** based on specific values (e.g., `where category == "Electronics"`) rather than searching through the whole database.

---

## 🛠️ Collection-Level Metadata Indexes

These are set within `inverted_index_config` at the collection level. They track "hidden" data about your objects. All three default to `False` and **cannot be changed after the collection is created**, so decide at creation time.

- **`indexTimestamps`**: Indexes `creationTimeUnix` and `lastUpdateTimeUnix`. Example → Essential for "Get latest 10 items" in queries. Enabled automatically if you configure object TTL.
- **`indexNullState`**: Tracks if a property is null. Required to use the `IsNull` filter operator (Python: `Filter.by_property("x").is_none(True)`).
- **`indexPropertyLength`**: Indexes the length of the data. Useful for queries like "Find all articles where the content is longer than 500 characters."

---

## 🔠 Tokenization: The Match Logic

Tokenization dictates how Weaviate treats your text. **Query and Index use the same logic.** If you tokenize as `word`, a search for "Apple" will match "apple!", but if you use `field`, it will not.

| **Method** | **Logic** | **Best For** |
| --- | --- | --- |
| **`word`** | Splits on non-alphanumeric, lowercases. | **General Text:** "The quick brown fox" |
| **`lowercase`** | Splits on whitespace only, lowercases. | **Technical Tags:** "user_name", "email@host.com" |
| **`whitespace`** | Splits on whitespace, **preserves case**. | **Case-sensitive IDs:** "ID_a123" vs "id_A123" |
| **`field`** | Entire value = One token. | **Exact Match:** SKU numbers, UUIDs, Slugs. |
| **`trigram`** | Splits into 3-character sequences. | **Fuzzy / partial match:** typos, substrings. Larger index. |
| **`gse`, `kagome_ja`, `kagome_kr`** | Language-specific segmenters. | **Chinese, Japanese, Korean** text. |

## 🚀 Optimization Checklist

### ✅ The Do's (Best Practices)

- `AUTOSCHEMA_ENABLED`: Set it to false to ensure that only objects matching your validated blueprint are ingested.
- **Manually Defining Properties**: allows you to define your data accurately, including adding `description` fields. While `description` is optional for simple schemas, it is important for **Query Agents** to accurately navigate and select the right properties in complex or unconventional datasets, which significantly improves search efficiency and accuracy.
- **Unused Indexes:** Explicitly set `indexSearchable`, `indexFilterable`, or `indexRangeFilters` to `False` if you do not plan to use that specific query type for a property; this reduces the number of physical data structures Weaviate must maintain.
- **Enable Range Filters for Comparison Operators:** You can enable `indexRangeFilters` for `int`, `number`, or `date` types. While not strictly required for range queries, enabling it is highly recommended as it makes "greater than" or "less than" operations significantly faster in most cases.
- **BlockMax WAND:** On by default for new collections since v1.30. It significantly speeds up BM25 and hybrid searches by skipping irrelevant data blocks. Collections created before v1.30 need to be migrated to use it.
- **Set Language-Specific Stopwords:** Use the `stopwords_preset` (like `"en"`) to prevent common words from slowing down relevance ranking. Since v1.37 you can also set a stopword preset per property (`text_analyzer.stopword_preset`, `word` tokenization only).
- **Choose Tokenization Based on Match Intent:** Use `word` for general text, but use `field` for identifiers (like SKUs or UUIDs or Entire String) to ensure exact matching and avoid splitting unique codes into multiple tokens.
- For Querying where combined text or exact string matching are needed, duplicate the property using different tokenization types (WORD and Field). Minimize this approach as it increases storage overhead, but it can be useful in very specific cases.

---

### ❌ The Don'ts (Common Pitfalls)

- **Don't Ignore the "Rule of One":** Avoid enabling multiple index types on a single property unless strictly necessary; for example, enabling both Searchable and Filterable on a long text field doubles the storage and indexing work.
- **Having Too Many Properties**: can lead to several performance and resource issues.
- **Don't Enable Metadata Indexes Blindly:** Avoid setting `indexTimestamps`, `indexNullState`, or `indexPropertyLength` to `True` unless your application specifically requires filtering by those attributes, as they add write overhead.
- **Don't Decide Metadata Indexes Later:** `indexNullState`, `indexPropertyLength` and `indexTimestamps` cannot be changed after the collection is created. If you need them later, you must create a new collection and re-import.
- **Don't Use `word` Tokenization for Entire String Match or Technical IDs:** Never use the default `word` tokenization for IDs with symbols (like `part-123_final`); it splits the ID into separate tokens, so an `Equal` filter also matches other values that contain the same tokens.
- **Don't Assume Stopwords Apply to All Tokenization:** Remember that stopwords are only removed when tokenization is set to `word`; they are NOT ignored if you use `field`, `whitespace`, or `lowercase`.
- **Don't Rely on `cleanup_interval_seconds`:** At collection level (`inverted_index_config`) it has no effect on the inverted index. LSM segment compaction and cleanup run automatically.

---

## 💻 Implementation Example: Optimized E-Commerce Schema

This example demonstrates a "real-world" developer configuration where we disable unnecessary indexes to save resources.

```python
# Create an example collection with optimized Inverted indexing configuration
from weaviate.classes.config import (
    Configure,
    DataType,
    Property,
    Tokenization,
    StopwordsPreset
)

result = client.collections.create(
    "Product",
    properties=[
        Property(
            name="name",
            data_type=DataType.TEXT,
            index_searchable=True,   # Searchable for BM25 "e.g. Smartphones"
            index_filterable=True,   # Filterable for "e.g. Name == X"
            tokenization=Tokenization.WORD
        ),
        Property(
            name="sku_id",
            data_type=DataType.TEXT,
            index_searchable=False,  # OPTIMIZATION: Don't need BM25 on a SKU
            index_filterable=True,   # Need to filter by ID
            tokenization=Tokenization.FIELD # Exact match only
        ),
        Property(
            name="price",
            data_type=DataType.NUMBER,
            index_filterable=False,   # OPTIMIZATION: range index also handles "Price == 100"
            index_range_filters=True  # For "Price < 50" and "Price == 100"
        ),
        Property(
            name="description",
            data_type=DataType.TEXT,
            index_searchable=True,
            index_filterable=False,   # OPTIMIZATION: We never do "Where description == ..."
        )
    ],
    # Collection-level configuration for metadata and global search behavior
    inverted_index_config=Configure.inverted_index(
        # METADATA INDEXES (cannot be changed after creation)
        index_null_state=True,        # Allows finding products with missing data
        index_timestamps=True,        # Sort by latest update/creation
        index_property_length=False,  # Optimization: we don't filter by character count

        # SEARCH RELEVANCE & STOPWORDS
        stopwords_preset=StopwordsPreset.EN, # Uses standard English list
        stopwords_additions=["promotion", "sale", "seasonal"], # custom noise words for this domain
        stopwords_removals=["the"], # ensure 'the' is actually indexed if needed
        bm25_k1=1.2,
        bm25_b=0.75
    )
)

print(f"Collection created successfully: {result.name}")
```

### 📁 Physical Index Separation

In Weaviate, an inverted index is not a single file; it is a collection of separate physical data structures. Each property and each index type (searchable, filterable, rangeable, nullState, propertyLength) creates its own unique directory and **Segment (LSM)** files.

- **`property_<name>` (Filterable):** Created when `indexFilterable` is true. This is a Roaring Bitmap used for rapid "yes/no" matching of specific values.
- **`property_<name>_searchable`:** Created when `indexSearchable` is true. This stores the map of words required for BM25 and Hybrid search.
- **`property_<name>_rangeable`:** Created when `indexRangeFilters` is true.
- **`property_<name>_nullState`:** Created only if `indexNullState` is enabled to track which objects have missing values.
- **`property_<name>_propertyLength`:** Created only if `indexPropertyLength` is enabled.

---

### 🛑 Why Stopwords Matter (and how they are ignored)

- Stopwords **are** actually indexed (they exist in the physical file), but they are **ignored during the query tokenization phase.**
    - **Why?** If you search for "The Apple iPhone", Weaviate sees "The" is a stopword and removes it from your search intent. It only looks for "Apple" and "iPhone" in the index.
- **The Performance Impact:** Common words like "the" or "and" appear in almost every document. If Weaviate had to score every document containing "the," your BM25 query would take seconds instead of milliseconds.
- **Important Caveat:** Stopwords are **only** removed if the tokenization is set to `word`. If you use `field` or `lowercase`, stopwords are treated like any other string and are **not** ignored.
