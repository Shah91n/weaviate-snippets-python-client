# 🤖 Query Agent: Optimization Guide

The essential best practices for maximizing the performance of Weaviate Query Agent. By refining schema and property scope, you can significantly improve search accuracy and ensure low-latency responses.

---

## 1. Foundation

A well-defined schema is the key point for the Query Agent. If the schema is vague, the agent will struggle with the data.

### Data Type Precision

The Agent relies on data types to construct queries. Using the wrong type leads to poor performance and bad results.

- **Numeric:** Use `int` or `number` (never `text` for numbers).
- **Booleans:** Use `boolean` for true/false flags.
- **Dates:** Use `date` (never `text` for dates).
- **Text:** exclusively for actual strings.

### Critical to keep in mind:

- **Disable Auto-Schema:** Set `AUTOSCHEMA_ENABLED: false`. This prevents auto generated types which can be incorrect. It allows you to define everything as you see for your data.
- **Add Collection and Property Descriptions:** This is the most underrated optimization. The Agent uses the collection `description` to decide which collection to query, and property descriptions to understand each property (e.g. units, meaning). Vague or missing descriptions are the most common cause of the Agent querying the wrong collection. Both can be updated after the collection is created.
    - *Bad:* `property: "temp"`
    - *Good:* `description: "The maximum operating temperature of the oven in Celsius"`

---

## 2. Efficiency: Property Management

For collections with many properties, the search space becomes too large for the Agent to process efficiently.

### Using `view_properties`

The most effective way to optimize is to define a **view window**. This limits the properties the Agent sees and considers during a query. If omitted, the Agent can view all properties. Example:

```python
from weaviate.agents.query import QueryAgent
from weaviate.agents.classes import QueryAgentCollectionConfig

qa = QueryAgent(
    client=client,
    collections=[
        QueryAgentCollectionConfig(
            name="YourCollection",
            # Include only essential properties to boost speed and accuracy
            view_properties=[
                "name",
                "category",
                "price",
                "brand",
                "specifications"
            ],
        ),
    ],
)
```

> **Note:** The `view_properties` configuration must be set via the Python/TypeScript SDK; it cannot currently be configured through the Console UI.

---

## 3. DOs and DON'Ts

| **Action** | **✅ DO** | **❌ DON'T** |
| --- | --- | --- |
| **Data Types** | Use `int`/`number` for math operations. | Store price or weight as `text`. |
| **Descriptions** | Add detailed descriptions for the collection and every field. | Leave descriptions blank or unrelated values. |
| **Scaling** | Use `view_properties` for collections with many fields. | Let the Agent scan every field of a very wide collection. |
