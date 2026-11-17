# BONC Search Platform Glossary

A plain-language guide to the words and settings used in the configurable search platform and its Admin panel.

This guide is for both technical and non-technical operators. You do not need to know how the search code works to use it. Each entry explains what the setting controls, gives an example, and explains how to choose a value.

> **Important:** There is no single "best" configuration for every category. People, Jobs and Articles contain different information. Start with the suggested defaults below, test with real queries, and change one setting at a time.

## 1. The basic building blocks

| Term | Plain-language meaning | Example |
|---|---|---|
| **Search platform** | The system that finds records matching a user's words and filters. | A user searches for Python developers in Indore. |
| **Category** | A separate collection of records with its own fields and search settings. | People, Jobs or Articles. |
| **Record / document** | One item that can appear in search results. The platform calls it a document even when it represents a person or job. | One person, one job posting or one article. |
| **Field** | One piece of information stored on a record. | Name, Skills, City, Salary or Article Title. |
| **Field key** | The short internal name used by the API and configuration. It should stay stable. | `skills`, `city`, `salary_min`. |
| **Field label** | The readable name shown to an Admin or user. | “Minimum salary” is the label for `salary_min`. |
| **Source column** | The column name in the original database from which a field gets its value. | SQL column `KeySkills` is mapped to field key `skills`. |
| **Role** | A hint describing what kind of information a field contains. It helps mapping and query understanding. | A City field has the `location` role. |
| **Index** | The prepared copy of category records that search uses to find results quickly. | The People index contains the records last synchronized for People. |
| **Index version** | A number that changes when the indexed records change. | Index version 12 means the data index has been refreshed 12 version steps. |
| **Configuration version** | A number that changes when the category's search settings are saved. | Configuration v4 is the fourth saved configuration. |
| **Source** | The original place where records come from. | A SQL Server table/view or an application that pushes records to the platform. |

### Field roles

The role is a description, not a score. Pick the role that best describes the information.

| Role | Use it for | Example |
|---|---|---|
| `title` | The main name/title of a record. Every category needs at least one title field. | Person Name, Job Title, Article Title. |
| `skill` | Skills, tools or areas of expertise. | Python, SQL, React. |
| `location` | A place or geographic location. | Indore, Bengaluru, Madhya Pradesh. |
| `category` | A classification or group. | Finance, Engineering, Technology. |
| `description` | Longer explanatory text. | Job description or article summary. |
| `general` | Information that does not fit another role. | Department, seniority or other metadata. |

## 2. Field settings

These settings are configured separately for each field.

| Setting | What it means | Allowed values / default | How to choose |
|---|---|---|---|
| **Searchable** | Whether the field's contents can help find a record through normal text search. | On/off; default **on**. | Turn on for information users are likely to type. Turn off when a field should not contribute to text matching. A field can still be filterable when Searchable is off. |
| **Semantic** | Whether the field is included when the system creates meaning-based search information. | On/off; default **on**. | Keep on for meaningful text such as titles and descriptions. Turn off for fields that are mainly codes or values where meaning-based matching is not useful. It only helps when the field is also searchable and has a weight above zero. |
| **Filterable** | Whether a user or search request is allowed to filter records using this field. | On/off; default **off**. | Turn on for fields users need to narrow results by, such as city, skill, status, work mode or salary. Do not enable it for sensitive information. |
| **Data type** | Tells the platform what kind of value the field stores, so it can compare and filter it correctly. | `text`, `keyword`, `integer`, `number`, `boolean`, `date`; default **text**. | Choose the type that matches the stored data. Examples are below. |
| **Weight** | How strongly this field should influence matching and ranking compared with other searchable fields. | Number from **0 to 100**; default **1**. | A **higher** value gives the field more influence. A value of **0** removes its contribution. There is no universal best value; use relative values and test them. |
| **Aliases** | Other words a user might use for this field. They help the platform understand the query. | Up to 20 words/phrases per field. | Add common alternatives, not unrelated words. Example: aliases `expertise` and `technology` for a Skills field, only when they make sense for your users. |

### Data type: which one should I choose?

| Type | Plain-language meaning | Example values | Suitable filters |
|---|---|---|---|
| `text` | Normal words or sentences. | “Backend developer”, “Builds web applications”. | Equal, not equal, contains, in-list, is empty / is not empty. |
| `keyword` | A short, distinct label or value. | “Indore”, “Full-time”, “Python”, “Active”. | Equal, not equal, contains, in-list, is empty / is not empty. |
| `integer` | A whole number with no decimal part. | 2 years, 15 openings, 8. | Equal, not equal, greater/less than or equal, between, in-list, null checks. |
| `number` | A number that can contain decimal places. | 8.5, 45000.50. | Equal, not equal, greater/less than or equal, between, in-list, null checks. |
| `boolean` | A yes/no or true/false value. | Remote: true; Closed: false. | Equal, not equal, in-list, null checks. |
| `date` | A date or date-and-time value. | `2026-11-17` or an ISO date/time. | Equal, not equal, greater/less than or equal, between, in-list, null checks. |

**Important:** Use `integer` or `number` for salary and experience when the source stores actual numeric values. Store dates in a consistent date/date-time format. Do not label a text column as a number just because it contains digits.

### Weight: what does 1, 2, 3, 5 or 10 mean?

Weight is an **importance setting**, not a mark out of 100 and not a measure of data quality.

- **Higher = more influence.** A field with weight 5 is given more importance than a field with weight 1.
- **Lower = less influence.** A field with weight 1 has less influence than a field with weight 5.
- **0 = no contribution** from that field to the weighted search signals.
- The valid range is 0–100, and decimal values are allowed.
- Do not set every field to 100. The values are compared with the other searchable fields and affect how the available matching signals are balanced.

A possible starting example for a People category is:

| Field | Example weight | Reason |
|---|---:|---|
| Name | 5 | Finding the correct person by name is important. |
| Skills | 4 | Skills are a major part of a people search. |
| Location | 3 | Location can help distinguish otherwise similar people. |
| Biography | 1 | Useful supporting information, but usually less important than name or skills. |

This is only a **starting experiment**, not a universal recommendation. For Jobs, a title or required skills may deserve more weight. For Articles, title and body/summary may deserve different weights. Compare actual search results before deciding.

## 3. How a field's query filter behaviour works

A field can be recognized in a natural-language query. For example, the platform may recognize “Python” as a Skills value and “Indore” as a Location value.

**Query filter mode** decides what happens after a value is recognized.

| Mode | Meaning | Effect |
|---|---|---|
| `none` | Do not automatically filter or give a special preference for this field. | The words can still participate in normal search matching. |
| `preferred` | Prefer records that match the recognized value, but do not remove records that do not match it. | Matching records can receive a small ranking bonus. |
| `required` | Treat the recognized value as a must-have condition. | Records that do not satisfy the recognized constraint are removed before ranking. |

**Default:** `none`.

**Which is best?** Use `required` only when the user clearly expects an exact restriction. Use `preferred` when the value is useful but should not exclude otherwise useful results. Use `none` when automatic field filtering is not wanted.

Automatic `required` query filters currently support **text, keyword and boolean** fields. Numeric and date ranges should be sent as explicit filters; the platform must not guess that mentioning a number means an exact equality filter.

### Query match: `all` or `any`

This setting controls how multiple recognized values in the *same field* are matched.

| Option | Meaning | Example |
|---|---|---|
| `all` | The record must contain every recognized value for that field. | “Python, SQL” requires both Python and SQL in Skills. |
| `any` | The record only needs at least one of the recognized values. | “Python, SQL” can match a record with Python or a record with SQL. |

**Default:** `all`.

Choose `all` when the user is asking for every listed skill or requirement. Choose `any` when the list means alternatives. Required constraints across different fields are combined: a record must satisfy each required field constraint.

## 4. Search methods and ranking settings

The platform can use two matching methods together.

| Term | Plain-language meaning |
|---|---|
| **Lexical search** | Looks for matching words and phrases in the fields. It is useful for exact terms, names, skills and identifiers. |
| **Semantic search** | Looks for similarity in meaning, even when the exact words differ. It uses the configured embedding model and vector index. |
| **Hybrid search** | Uses lexical and semantic signals together to rank results. |
| **Ranking** | The order in which matching records are shown. |
| **Score** | A number used to order results. It is not a probability that a result is correct. |

### Category search rules

| Setting | Range / default | What happens when changed |
|---|---|---|
| **Lexical enabled** | On/off; default **on**. | Turns word/phrase matching on or off. Keep at least one search method enabled. |
| **Semantic enabled** | On/off; default **on**. | Turns meaning-based matching on or off. Keep at least one search method enabled. |
| **Lexical weight** | 0–1; default **0.65**. | A higher value gives word/phrase matching more influence relative to semantic matching. |
| **Semantic weight** | 0–1; default **0.35**. | A higher value gives meaning-based matching more influence relative to lexical matching. |
| **Semantic minimum score** | 0–100; default **20**. | Semantic matches below this score are discarded. A higher value is stricter; a lower value allows weaker semantic candidates. |
| **Semantic high-confidence score** | 0–100; default **75**. | At or above this score, a semantic candidate can pass the semantic relevance safeguard without shared-word overlap. A higher value makes this exception harder to reach; a lower value makes it easier. |
| **Semantic candidate limit** | 1–200; default **50**. | Maximum number of nearest semantic candidates considered. A higher value considers more possible results but can take more work. A lower value is narrower and may miss relevant candidates. |

### How should I set lexical and semantic weights?

The defaults are **0.65 lexical** and **0.35 semantic**. They are relative weights, not percentages that must add up to exactly 1. The system combines the enabled signals using their weights.

- More exact matching needed? Test a higher lexical weight.
- More meaning-based matching needed? Test a higher semantic weight.
- Do not assume one is always better. The best balance depends on the category and must be measured against known queries.
- If a category has no usable semantic vectors, the system avoids reducing the lexical score just because semantic search is enabled.

A good starting point is to keep the defaults until an evaluation shows a problem.

### Other ranking terms

| Term | Plain-language meaning |
|---|---|
| **Exact identifier match** | The query exactly matches a record's ID. This result is placed ahead of ordinary ranked matches. |
| **Matched fields** | Fields where the search found a lexical match. |
| **Phrase score** | An extra signal for finding a phrase in the same field. It can add up to 10 points to the lexical score. |
| **Preferred filter bonus** | A small ranking bonus for matching a field configured as preferred. It can add up to 8 points. |
| **Semantic candidate** | A record returned by the meaning-based search before later relevance checks and ranking. |
| **Semantic safeguard** | A check that rejects moderate semantic matches that share no meaningful word with the searchable field values. High-confidence matches can pass this check. |
| **Synonym group** | Words configured as alternatives for the same concept, such as “developer” and “programmer”. The platform expands a recognized term with its alternatives. |
| **Spelling correction** | A correction applied to residual query words using that category's indexed vocabulary. Exact indexed values are protected from being rewritten. |

The final score is capped at 100. **A score of 90 does not mean a 90% chance of being correct.** Use relevance labels and evaluation metrics to judge quality.

## 5. Filters: two different places they can be applied

This distinction is important.

### A. Search-result filters

These are applied when someone performs a search. They narrow the results for that particular request; they do not change the underlying source data.

| Operator | Meaning | Example |
|---|---|---|
| `eq` | Is equal to | City is Indore. |
| `ne` | Is not equal to | Status is not Closed. For a multi-value field, none of its values may equal the excluded value. |
| `in` | Matches at least one value in a supplied list | Work mode is one of Remote or Hybrid. |
| `contains` | Contains the supplied text/phrase | Description contains “backend developer”. |
| `gte` | Greater than or equal to | Experience is at least 3. |
| `lte` | Less than or equal to | Salary is at most 100000. |
| `between` | Within an inclusive lower and upper bound | Salary is between 50000 and 100000. |
| `is_null` | Has no value | Closing date is empty. |
| `not_null` | Has a value | Closing date is present. |

The operator list depends on the field's data type. For example, `contains` is for text/keyword fields; numeric and date fields support range operators. All explicit filters in one request must match.

### B. SQL source filters

These are configured on a SQL Server source. They decide which source records are copied into the category index during synchronization.

Supported operators are `eq`, `ne`, `in`, `is_null` and `not_null`.

Example: only synchronize source rows where `IsDeleted = 0`.

Changing a SQL source filter affects the data brought into the index on the next sync. It is **not** the same as a filter a user applies to one search.

## 6. Data sources and synchronization

| Term | Plain-language meaning |
|---|---|
| **SQL Server source** | A table or view in a SQL Server database that supplies category records. |
| **Push source** | A source where another application sends already prepared records to the platform. |
| **Connection profile** | The server-side name for a saved database connection. Its password/connection string stays on the server; it is not entered into the category field mapping. |
| **Schema** | A namespace inside a database, commonly `dbo`. |
| **Table / view** | The database object from which records are read. A view can combine information from multiple tables. |
| **Document ID column** | The source column used to identify one record. Each ID must be present, non-blank and unique in the rows being synchronized. |
| **Source mapping** | The link between a source column and a category field. |
| **Preflight / source validation** | A safety check before synchronization. It checks the selected source, ID column, row count and ID quality. |
| **Join-view preview** | Generates a SQL script for review. It does not execute the script or create the view automatically. |
| **Sync / synchronize** | Reads source records and refreshes the category's stored search documents. |
| **Full snapshot sync** | Reads the selected source rows and replaces the category's existing document snapshot. This is the current SQL Server sync behaviour; incremental sync is not implemented yet. |
| **Ingest** | Adds or updates documents sent directly to the platform through the Admin API. |
| **Replace all** | Deletes the existing category documents and stores the supplied batch as the new snapshot. Use carefully. |

### Maximum records

The SQL source setting **Max records** controls the maximum number of source rows allowed for one synchronization.

- Valid range: **1 to 100,000**.
- Default: **50,000**.
- Higher allows more records but can increase database work, index-build time and memory use.
- Lower limits the amount of data synchronized.
- There is no universally best number. Set it based on the expected category size and available server capacity.

The system refuses to replace the existing snapshot when preflight fails or the source query returns no records.

## 7. Search request and result terms

| Term | Range / default | Meaning |
|---|---|---|
| **Query** | 1–500 characters | The words a user wants to search for. |
| **Category** | A category ID | Selects which category's records and settings to use. |
| **Page** | Starts at 1; default **1** | Which page of results to show. |
| **Page size** | 1–100; default **10** | How many results to return on a page. A larger page returns more records per request. |
| **Explicit filters** | Up to 20 filters per request | Extra conditions applied to the current search. |
| **Total count** | Read-only result value | Number of records that matched and are eligible after filters. |
| **Zero-result query** | No matching records returned | A useful signal to investigate: the wording, configuration, data or filters may need attention. |

## 8. Admin, privacy and feedback terms

| Term | Plain-language meaning |
|---|---|
| **Active category** | A category enabled for search use. |
| **Disabled category** | A category that is not available for search. |
| **Public search enabled** | Allows the public category-search endpoint to return this category's records. The default is **off**. Only enable it after reviewing the data and access requirements. |
| **Admin API token** | A secret used to authorize Admin operations. Keep it on the server or in a secure environment variable; never put it in source control or browser-visible code. |
| **Configuration history** | Saved snapshots showing earlier category configurations, who changed them, and any supplied reason. The current panel displays this history; it is not an automatic rollback button. |
| **Relevant / not relevant feedback** | A human label indicating whether a returned record was useful for a query. |
| **Feedback summary** | Counts and saved labels collected from Admin search testing. |
| **Automatic learning** | The system changing its ranking by itself from feedback. This is **not implemented**; feedback is currently collected for later analysis and evaluation. |

**Privacy reminder:** Keep People search private unless the data owner explicitly approves public exposure and the fields have been reviewed. Do not map private contact details, passwords, or other sensitive fields into a publicly searchable category.

## 9. A simple setup checklist for a new category

1. Choose a clear category name, such as People, Jobs or Articles.
2. Select a stable, unique document ID column.
3. Map the fields users will search or filter by.
4. Choose each field's role and data type correctly.
5. Enable Searchable for fields users should find by typing words.
6. Enable Semantic for meaningful text where meaning-based matches help.
7. Set Filterable only for fields users need to narrow results by.
8. Start field weights with relative values, not all 100.
9. Use `preferred` for helpful hints and `required` only for genuine must-have conditions.
10. Validate the source before syncing.
11. Test known queries and check the actual returned records.
12. Keep public search disabled until privacy and access have been approved.

## 10. What to do when results are poor

| What you see | First things to check |
|---|---|
| A relevant record is missing | Is its source row indexed? Is the correct field mapped and Searchable? Is the field's weight too low? Are filters excluding it? |
| Irrelevant records rank too high | Are broad descriptions overweighted? Are aliases/synonyms too broad? Is the semantic threshold too permissive? |
| A required term does not filter | Is the field Filterable, set to Required, and using a supported type? Was the exact value found in the index? |
| A numeric/date filter fails | Is the field typed as integer, number or date? Is the filter value formatted correctly? |
| No records appear | Check the category is active, has indexed records, and the filters are not too restrictive. |
| A new database value is not found | Has the category been synchronized since the source changed? |
| Results change after a configuration save | Check the configuration version and whether the field weights, search settings or filter behaviour changed. |

For formal comparison, use the Search Platform Evaluation Guide and the evaluation script rather than judging only by one or two example queries.

## 11. More words you may see in Admin and search diagnostics

| Term | Plain-language meaning |
|---|---|
| **API** | Application Programming Interface. A defined way for one software system to ask another system to do something or return information. |
| **Endpoint** | A particular API address for one action, such as searching a category or validating a database source. |
| **Query** | The words and conditions entered by the person searching. |
| **Query plan** | The platform's explanation of how it interpreted a query and which matching/filtering steps it applied. It is diagnostic information, not a separate search result. |
| **Free-text query** | A query where no configured field alias or exact indexed field value was recognized. The full wording is treated as a general search. |
| **Field-guided query** | A query where the system recognized at least one configured field alias or exact indexed value, such as a skill or city. |
| **Matched alias** | A word recognized as another name for a field. Example: “expertise” is an alias for Skills. |
| **Matched value** | A value found in the category's indexed records and recognized in the query. Example: “Indore” is an indexed City value. |
| **Unmatched token** | A meaningful word in the query that was not recognized as a configured alias or exact indexed value. It can still help normal search. |
| **Corrected query** | The query text after the platform applies any permitted spelling corrections to residual words. Exact indexed values are protected. |
| **Expanded query / synonym expansion** | Extra alternative words added from a category's synonym settings. Example: a configured “developer” synonym group can add “programmer” as an alternative. |
| **Hard filter** | A must-have condition. Records that do not satisfy it are removed before ranking. |
| **Ranking hint** | A helpful clue that can improve a record's position without removing other records. |
| **Eligible document count** | Number of indexed records left after required automatic filters and explicit result filters are applied. |
| **Filtered document count** | Number of indexed records excluded by those filters. |
| **Candidate** | A possible matching record retrieved by one of the search methods before the final results are ordered. |
| **Token** | A word or searchable piece of a query. Search may split text into tokens to find matching records. |
| **Inverted index** | A lookup structure that helps the platform quickly find which records contain a word. Think of it as the index at the back of a book. |
| **Embedding** | A numerical representation of text that helps the computer compare meaning, not just identical words. |
| **Vector** | The list of numbers used to represent an embedding. Similar vectors indicate text that may have similar meaning. |
| **FAISS** | A software library used by this platform to search through vectors efficiently. It is part of the meaning-based search implementation. |
| **Model** | The text-understanding component that creates embeddings. The current generic search uses the configured sentence-transformers embedding model; it is not automatically trained from Admin feedback. |
| **Preflight warning** | A problem or risk found during source validation that should be reviewed before syncing. |
| **Primary key** | A database column, or group of columns, that identifies a row in its own table. It is often a good starting point for a document ID, after validation. |
| **Foreign key** | A database link from a row in one table to a related row in another table. |
| **Relationship** | The defined connection between tables, usually described through foreign keys. |
| **Join** | A database operation that brings related information from two or more tables together. |
| **Left join** | A join that keeps every row from the selected starting table, even when there is no matching row in the related table. |
| **One-to-many relationship** | One row on one side can be connected to many rows on the other side. Joining it carelessly can duplicate search documents. |
| **Many-to-one relationship** | Many rows can refer to one row in another table. This is often safer when enriching one base record with parent information. |
| **Base table** | The table whose row represents one search document in a generated join-view preview. |
| **View** | A saved database query that behaves like a table when read. A view can combine selected columns from several tables. |

## 12. Relative field-weight starting examples

The examples below are deliberately relative. They are not preconfigured category values and should be confirmed with evaluation queries.

| Category | Field | Example starting weight |
|---|---|---:|
| People | Name | 5 |
| People | Skills | 4 |
| People | Location | 3 |
| People | Biography | 1 |
| Jobs | Job title | 5 |
| Jobs | Required skills | 4 |
| Jobs | Location | 3 |
| Jobs | Description | 1 |
| Articles | Article title | 5 |
| Articles | Tags / topic | 4 |
| Articles | Summary | 3 |
| Articles | Full body | 1 |

A field's weight should reflect how important that information is when deciding whether a record answers the query. It should not simply be set higher because the field contains more text.

### A few validation rules to remember

- At least one field must be Searchable and every category must have a Title-role field.
- Field keys and source columns must be unique within a category.
- At least one of Lexical search or Semantic search must be enabled.
- If a search method is enabled, its weight must be greater than zero. Do not set an enabled method's weight to zero; disable that method instead.
- A Required automatic query filter cannot be configured for integer, number or date fields. Use explicit typed range filters for those fields.
- A query can contain both automatic constraints and explicit filters. Across different required fields and explicit filters, all conditions must be satisfied.
- A source document ID must be present, non-blank and unique in the selected source rows before synchronization.
