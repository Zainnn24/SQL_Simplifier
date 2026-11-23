-- DuckDB examples for the 25 SQL predicate simplification families
-- Run with the DuckDB CLI in a fresh in-memory database:
--     duckdb < SQL_Predicate_Family_Examples.sql
-- Each labeled block also runs on its own in a fresh session. The transaction rolls back
-- its table and sample data so the next example can reuse a table name.
-- Each SELECT returns row IDs. The expected IDs in the comments are independent of row order.
-- All tables, fixtures and complete DuckDB statements were assembled for this project.
-- The provenance note identifies upstream test adaptations and new instances of source rules.
-- For an LLM trial send only CREATE TABLE, the original SELECT and generic task instructions.
-- Keep comments, sample rows, expected outputs and reference answers outside the prompt.
-- Use these 25 pairs as one seed per family in the planned 100-task benchmark.

-- A. Boolean structure

-- A1. Repeated condition
-- Test adaptation | SQLGlot, Comparison and Pruning (repeated conjuncts).
-- SQLGlot optimizer tests, simplify.sql. See the named section above.
-- https://github.com/tobymao/sqlglot/blob/main/tests/fixtures/optimizer/simplify.sql
-- Repeating the priority threshold cannot change which tickets qualify.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 2, 3.
BEGIN TRANSACTION;
CREATE TABLE tickets (
    row_id BIGINT NOT NULL,
    priority BIGINT NOT NULL
);
INSERT INTO tickets VALUES
    (1, 3),
    (2, 4),
    (3, 5);

-- Original
SELECT row_id
FROM tickets
WHERE priority >= 4 AND priority >= 4;

-- Smaller reference (hide during generation)
SELECT row_id
FROM tickets
WHERE priority >= 4;

ROLLBACK;

-- A2. Absorption
-- Test adaptation | SQLGlot, Absorption. The reference applies the identity fully.
-- SQLGlot optimizer tests, simplify.sql. See the named section above.
-- https://github.com/tobymao/sqlglot/blob/main/tests/fixtures/optimizer/simplify.sql
-- Jarke and Koch, Query Optimization in Database Systems, Table 2 and discussion of comparison semantics.
-- https://www.csd.uoc.gr/~hy460/pdf/computersurveys84.pdf
-- Every row in the discounted branch already qualifies on units.
-- Predicate AST nodes: 11 -> 3. Expected row IDs from each SELECT: 3, 4.
BEGIN TRANSACTION;
CREATE TABLE orders (
    row_id BIGINT NOT NULL,
    units BIGINT NOT NULL,
    discount_pct BIGINT NOT NULL
);
INSERT INTO orders VALUES
    (1, 9, 0),
    (2, 9, 5),
    (3, 10, 0),
    (4, 10, 5);

-- Original
SELECT row_id
FROM orders
WHERE units >= 10 OR (units >= 10 AND discount_pct > 0);

-- Smaller reference (hide during generation)
SELECT row_id
FROM orders
WHERE units >= 10;

ROLLBACK;

-- A3. Common factor
-- Rule instance | distributivity, applied in the factoring direction.
-- Jarke and Koch, Query Optimization in Database Systems, Table 2 and discussion of comparison semantics.
-- https://www.csd.uoc.gr/~hy460/pdf/computersurveys84.pdf
-- Both alternatives require priority 1.
-- Predicate AST nodes: 15 -> 11. Expected row IDs from each SELECT: 6, 7, 8.
BEGIN TRANSACTION;
CREATE TABLE deliveries (
    row_id BIGINT NOT NULL,
    priority BIGINT NOT NULL,
    distance_km BIGINT NOT NULL,
    delay_min BIGINT NOT NULL
);
INSERT INTO deliveries VALUES
    (1, 0, 40, 14),
    (2, 0, 40, 15),
    (3, 0, 41, 14),
    (4, 0, 41, 15),
    (5, 1, 40, 14),
    (6, 1, 40, 15),
    (7, 1, 41, 14),
    (8, 1, 41, 15);

-- Original
SELECT row_id
FROM deliveries
WHERE (priority = 1 AND distance_km > 40) OR (priority = 1 AND delay_min >= 15);

-- Smaller reference (hide during generation)
SELECT row_id
FROM deliveries
WHERE priority = 1 AND (distance_km > 40 OR delay_min >= 15);

ROLLBACK;

-- A4. Negation of two negated conditions
-- Rule instance | De Morgan and double negation.
-- Jarke and Koch, Query Optimization in Database Systems, Table 2 and discussion of comparison semantics.
-- https://www.csd.uoc.gr/~hy460/pdf/computersurveys84.pdf
-- A row qualifies when either threshold is met.
-- Predicate AST nodes: 10 -> 7. Expected row IDs from each SELECT: 2, 3, 4.
BEGIN TRANSACTION;
CREATE TABLE readings (
    row_id BIGINT NOT NULL,
    temp_c BIGINT NOT NULL,
    pressure_kpa BIGINT NOT NULL
);
INSERT INTO readings VALUES
    (1, 70, 199),
    (2, 70, 200),
    (3, 71, 199),
    (4, 71, 200);

-- Original
SELECT row_id
FROM readings
WHERE NOT ((NOT (temp_c > 70)) AND (NOT (pressure_kpa >= 200)));

-- Smaller reference (hide during generation)
SELECT row_id
FROM readings
WHERE temp_c > 70 OR pressure_kpa >= 200;

ROLLBACK;

-- A5. Branch covered by two other branches
-- Rule instance | consensus theorem.
-- National Taiwan University, Logic Design, Unit 3, Consensus Theorem.
-- https://cc.ee.ntu.edu.tw/~jhjiang/instruction/courses/fall13-ld/unit03.pdf
-- If both wait and retry conditions hold, either value of the priority test already selects the
-- row.
-- Predicate AST nodes: 24 -> 16. Expected row IDs from each SELECT: 2, 4, 7, 8.
BEGIN TRANSACTION;
CREATE TABLE requests (
    row_id BIGINT NOT NULL,
    priority BIGINT NOT NULL,
    wait_min BIGINT NOT NULL,
    retries BIGINT NOT NULL
);
INSERT INTO requests VALUES
    (1, 0, 9, 0),
    (2, 0, 9, 1),
    (3, 0, 10, 0),
    (4, 0, 10, 1),
    (5, 1, 9, 0),
    (6, 1, 9, 1),
    (7, 1, 10, 0),
    (8, 1, 10, 1);

-- Original
SELECT row_id
FROM requests
WHERE (priority = 1 AND wait_min >= 10) OR (NOT (priority = 1) AND retries > 0) OR (wait_min >= 10 AND retries > 0);

-- Smaller reference (hide during generation)
SELECT row_id
FROM requests
WHERE (priority = 1 AND wait_min >= 10) OR (NOT (priority = 1) AND retries > 0);

ROLLBACK;

-- B. Single-column comparisons and bounds

-- B1. Stronger bound implies a weaker one
-- Test adaptation | Calcite, testSimplifyFilter, two lower bounds.
-- Apache Calcite, RexProgramTest.java, commit 57ad4ff. See the named test above.
-- https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1133-L1137
-- The higher cutoff implies the lower one.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 4.
BEGIN TRANSACTION;
CREATE TABLE shipments (
    row_id BIGINT NOT NULL,
    weight_kg BIGINT NOT NULL
);
INSERT INTO shipments VALUES
    (1, 5),
    (2, 6),
    (3, 20),
    (4, 21);

-- Original
SELECT row_id
FROM shipments
WHERE weight_kg > 20 AND weight_kg > 5;

-- Smaller reference (hide during generation)
SELECT row_id
FROM shipments
WHERE weight_kg > 20;

ROLLBACK;

-- B2. Equality settles another comparison
-- Rule instance | comparison semantics with a fixed value.
-- Jarke and Koch, Query Optimization in Database Systems, Table 2 and discussion of comparison semantics.
-- https://www.csd.uoc.gr/~hy460/pdf/computersurveys84.pdf
-- A grade fixed at 8 is already below 12.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 2.
BEGIN TRANSACTION;
CREATE TABLE students (
    row_id BIGINT NOT NULL,
    grade_level BIGINT NOT NULL
);
INSERT INTO students VALUES
    (1, 7),
    (2, 8),
    (3, 11),
    (4, 12);

-- Original
SELECT row_id
FROM students
WHERE grade_level = 8 AND grade_level < 12;

-- Smaller reference (hide during generation)
SELECT row_id
FROM students
WHERE grade_level = 8;

ROLLBACK;

-- B3. Bounds pin one value
-- Test adaptation | Calcite, testSimplifyFilter, equal lower and upper endpoints.
-- Apache Calcite, RexProgramTest.java, commit 57ad4ff. See the named test above.
-- https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1078-L1079
-- Only the shared endpoint satisfies both comparisons.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 2, 4.
BEGIN TRANSACTION;
CREATE TABLE parcels (
    row_id BIGINT NOT NULL,
    weight_kg BIGINT NOT NULL
);
INSERT INTO parcels VALUES
    (1, 14),
    (2, 15),
    (3, 16),
    (4, 15);

-- Original
SELECT row_id
FROM parcels
WHERE weight_kg >= 15 AND weight_kg <= 15;

-- Smaller reference (hide during generation)
SELECT row_id
FROM parcels
WHERE weight_kg = 15;

ROLLBACK;

-- B4. Exclude an endpoint
-- Rule instance | inclusive and exclusive range endpoints.
-- PostgreSQL 17, sections 8.17.3 and 8.17.7 on endpoints and discrete ranges.
-- https://www.postgresql.org/docs/17/rangetypes.html
-- Excluding zero converts an inclusive lower bound to a strict one.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 3.
BEGIN TRANSACTION;
CREATE TABLE accounts (
    row_id BIGINT NOT NULL,
    balance_cents BIGINT NOT NULL
);
INSERT INTO accounts VALUES
    (1, -1),
    (2, 0),
    (3, 1);

-- Original
SELECT row_id
FROM accounts
WHERE balance_cents >= 0 AND balance_cents != 0;

-- Smaller reference (hide during generation)
SELECT row_id
FROM accounts
WHERE balance_cents > 0;

ROLLBACK;

-- B5. The integer type guarantees a bound
-- Documentation adaptation | MySQL type-limit folding, instantiated for signed BIGINT NOT NULL.
-- MySQL 8.4 Reference Manual, section 10.2.1.14, Constant-Folding Optimization.
-- https://dev.mysql.com/doc/refman/8.4/en/constant-folding-optimization.html
-- Every allowed value is at most the maximum signed 64-bit integer.
-- Predicate AST nodes: 3 -> 1. Expected row IDs from each SELECT: 1, 2, 3, 4, 5.
BEGIN TRANSACTION;
CREATE TABLE counters (
    row_id BIGINT NOT NULL,
    counter_value BIGINT NOT NULL
);
INSERT INTO counters VALUES
    (1, -9223372036854775808),
    (2, -1),
    (3, 0),
    (4, 9223372036854775806),
    (5, 9223372036854775807);

-- Original
SELECT row_id
FROM counters
WHERE counter_value <= 9223372036854775807;

-- Smaller reference (hide during generation)
SELECT row_id
FROM counters
WHERE TRUE;

ROLLBACK;

-- C. Unions and gaps in integer ranges

-- C1. Merge overlapping intervals
-- Test adaptation | Calcite, testSimplifyRange3. Keep the disjoint tail and use non-null
-- values.
-- Apache Calcite, RexProgramTest.java, commit 57ad4ff. See the named test above.
-- https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1619-L1635
-- The first two ranges overlap. Readings of 40 or greater still qualify separately.
-- Predicate AST nodes: 19 -> 11. Expected row IDs from each SELECT: 2, 3, 4, 5, 6, 7, 10.
BEGIN TRANSACTION;
CREATE TABLE samples (
    row_id BIGINT NOT NULL,
    reading BIGINT NOT NULL
);
INSERT INTO samples VALUES
    (1, 9),
    (2, 10),
    (3, 17),
    (4, 18),
    (5, 20),
    (6, 21),
    (7, 25),
    (8, 26),
    (9, 39),
    (10, 40);

-- Original
SELECT row_id
FROM samples
WHERE (reading >= 10 AND reading <= 20) OR (reading >= 18 AND reading <= 25) OR reading >= 40;

-- Smaller reference (hide during generation)
SELECT row_id
FROM samples
WHERE (reading >= 10 AND reading <= 25) OR reading >= 40;

ROLLBACK;

-- C2. Consecutive points form an interval
-- Rule instance | adjacency in a discrete integer domain.
-- PostgreSQL 17, sections 8.17.3 and 8.17.7 on endpoints and discrete ranges.
-- https://www.postgresql.org/docs/17/rangetypes.html
-- There is no integer between adjacent listed stages.
-- Predicate AST nodes: 11 -> 7. Expected row IDs from each SELECT: 2, 3, 4.
BEGIN TRANSACTION;
CREATE TABLE jobs (
    row_id BIGINT NOT NULL,
    stage BIGINT NOT NULL
);
INSERT INTO jobs VALUES
    (1, 3),
    (2, 4),
    (3, 5),
    (4, 6),
    (5, 7);

-- Original
SELECT row_id
FROM jobs
WHERE stage = 4 OR stage = 5 OR stage = 6;

-- Smaller reference (hide during generation)
SELECT row_id
FROM jobs
WHERE stage >= 4 AND stage <= 6;

ROLLBACK;

-- C3. Two ranges leave one point out
-- Test-input adaptation | SQLGlot, Comparisons. Its expected expression retains the two
-- comparisons. The smaller reference here is ours.
-- SQLGlot optimizer tests, simplify.sql. See the named section above.
-- https://github.com/tobymao/sqlglot/blob/main/tests/fixtures/optimizer/simplify.sql
-- Zero is the only integer outside both ranges.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 1, 3.
BEGIN TRANSACTION;
CREATE TABLE inventory (
    row_id BIGINT NOT NULL,
    stock_delta BIGINT NOT NULL
);
INSERT INTO inventory VALUES
    (1, -1),
    (2, 0),
    (3, 1);

-- Original
SELECT row_id
FROM inventory
WHERE stock_delta < 0 OR stock_delta > 0;

-- Smaller reference (hide during generation)
SELECT row_id
FROM inventory
WHERE stock_delta != 0;

ROLLBACK;

-- C4. Outer bound eliminates one branch
-- Rule instance | intersection of a bound with a union of ranges.
-- PostgreSQL 17, section 9.20, range intersection and difference.
-- https://www.postgresql.org/docs/17/functions-range.html
-- The outer bound removes the early interval and retains every value in the late interval.
-- Predicate AST nodes: 11 -> 3. Expected row IDs from each SELECT: 5, 6.
BEGIN TRANSACTION;
CREATE TABLE bookings (
    row_id BIGINT NOT NULL,
    lead_days BIGINT NOT NULL
);
INSERT INTO bookings VALUES
    (1, 2),
    (2, 3),
    (3, 5),
    (4, 8),
    (5, 9),
    (6, 10);

-- Original
SELECT row_id
FROM bookings
WHERE (lead_days <= 2 OR lead_days >= 9) AND lead_days >= 5;

-- Smaller reference (hide during generation)
SELECT row_id
FROM bookings
WHERE lead_days >= 9;

ROLLBACK;

-- C5. Excluded point already lies in the gap
-- Test adaptation | Calcite, testSimplifyRange. Retain the meaningful exclusion as a
-- comparison.
-- Apache Calcite, RexProgramTest.java, commit 57ad4ff. See the named test above.
-- https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1589-L1605
-- Excluding 11 does nothing because it lies in the gap. Excluding 4 affects the first interval.
-- Predicate AST nodes: 19 -> 15. Expected row IDs from each SELECT: 2, 3, 5, 6, 10.
BEGIN TRANSACTION;
CREATE TABLE measurements (
    row_id BIGINT NOT NULL,
    reading BIGINT NOT NULL
);
INSERT INTO measurements VALUES
    (1, 0),
    (2, 1),
    (3, 3),
    (4, 4),
    (5, 5),
    (6, 8),
    (7, 9),
    (8, 11),
    (9, 13),
    (10, 14);

-- Original
SELECT row_id
FROM measurements
WHERE ((reading > 0 AND reading <= 8) OR reading >= 14) AND reading != 4 AND reading != 11;

-- Smaller reference (hide during generation)
SELECT row_id
FROM measurements
WHERE ((reading > 0 AND reading <= 8) OR reading >= 14) AND reading != 4;

ROLLBACK;

-- D. Contradictions and impossible branches

-- D1. Incompatible equalities
-- Test adaptation | Calcite, testSimplifyFilter, incompatible values for one column.
-- Apache Calcite, RexProgramTest.java, commit 57ad4ff. See the named test above.
-- https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1098-L1101
-- No ticket can have both status codes.
-- Predicate AST nodes: 7 -> 1. Expected row IDs from each SELECT: (empty).
BEGIN TRANSACTION;
CREATE TABLE tickets (
    row_id BIGINT NOT NULL,
    status_code BIGINT NOT NULL
);
INSERT INTO tickets VALUES
    (1, 1),
    (2, 2),
    (3, 9);

-- Original
SELECT row_id
FROM tickets
WHERE status_code = 2 AND status_code = 9;

-- Smaller reference (hide during generation)
SELECT row_id
FROM tickets
WHERE FALSE;

ROLLBACK;

-- D2. Conflicting bounds
-- Test adaptation | Calcite, testSimplifyFilter, conflicting upper and lower bounds.
-- Apache Calcite, RexProgramTest.java, commit 57ad4ff. See the named test above.
-- https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1151-L1155
-- No number is below 3 and above 10.
-- Predicate AST nodes: 7 -> 1. Expected row IDs from each SELECT: (empty).
BEGIN TRANSACTION;
CREATE TABLE deliveries (
    row_id BIGINT NOT NULL,
    late_days BIGINT NOT NULL
);
INSERT INTO deliveries VALUES
    (1, 2),
    (2, 3),
    (3, 7),
    (4, 10),
    (5, 11);

-- Original
SELECT row_id
FROM deliveries
WHERE late_days < 3 AND late_days > 10;

-- Smaller reference (hide during generation)
SELECT row_id
FROM deliveries
WHERE FALSE;

ROLLBACK;

-- D3. Empty open integer interval
-- Rule instance | contradictory condition on a discrete domain.
-- PostgreSQL 17, sections 8.17.3 and 8.17.7 on endpoints and discrete ranges.
-- https://www.postgresql.org/docs/17/rangetypes.html
-- Brass and Goldberg, Semantic Errors in SQL Queries, errors 1 and 8.
-- https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf
-- The open interval between 12 and 13 contains no integer.
-- Predicate AST nodes: 7 -> 1. Expected row IDs from each SELECT: (empty).
BEGIN TRANSACTION;
CREATE TABLE batches (
    row_id BIGINT NOT NULL,
    quantity BIGINT NOT NULL
);
INSERT INTO batches VALUES
    (1, 11),
    (2, 12),
    (3, 13),
    (4, 14);

-- Original
SELECT row_id
FROM batches
WHERE quantity > 12 AND quantity < 13;

-- Smaller reference (hide during generation)
SELECT row_id
FROM batches
WHERE FALSE;

ROLLBACK;

-- D4. Equal columns have conflicting values
-- Test adaptation | Calcite, testSimplifyFilter, incompatible constants linked by equality. The
-- express branch is added.
-- Apache Calcite, RexProgramTest.java, commit 57ad4ff. See the named test above.
-- https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1111-L1113
-- Brass and Goldberg, Semantic Errors in SQL Queries, errors 1 and 8.
-- https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf
-- The equality makes the first branch impossible. The express branch survives.
-- Predicate AST nodes: 15 -> 3. Expected row IDs from each SELECT: 3, 4.
BEGIN TRANSACTION;
CREATE TABLE parcels (
    row_id BIGINT NOT NULL,
    sent_zone BIGINT NOT NULL,
    received_zone BIGINT NOT NULL,
    express_flag BIGINT NOT NULL
);
INSERT INTO parcels VALUES
    (1, 2, 4, 0),
    (2, 2, 2, 0),
    (3, 4, 4, 1),
    (4, 2, 4, 1);

-- Original
SELECT row_id
FROM parcels
WHERE (sent_zone = received_zone AND sent_zone = 2 AND received_zone = 4) OR express_flag = 1;

-- Smaller reference (hide during generation)
SELECT row_id
FROM parcels
WHERE express_flag = 1;

ROLLBACK;

-- D5. Ordering cycle contains a strict inequality
-- Rule instance | transitivity and an inconsistent subcondition.
-- Jarke and Koch, Query Optimization in Database Systems, Table 2 and discussion of comparison semantics.
-- https://www.csd.uoc.gr/~hy460/pdf/computersurveys84.pdf
-- Brass and Goldberg, Semantic Errors in SQL Queries, errors 1 and 8.
-- https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf
-- The cycle would force opened_day to be strictly less than itself.
-- Predicate AST nodes: 15 -> 3. Expected row IDs from each SELECT: 3, 4.
BEGIN TRANSACTION;
CREATE TABLE milestones (
    row_id BIGINT NOT NULL,
    opened_day BIGINT NOT NULL,
    reviewed_day BIGINT NOT NULL,
    closed_day BIGINT NOT NULL,
    urgent BIGINT NOT NULL
);
INSERT INTO milestones VALUES
    (1, 1, 1, 1, 0),
    (2, 1, 2, 3, 0),
    (3, 3, 2, 1, 1),
    (4, 4, 4, 4, 1);

-- Original
SELECT row_id
FROM milestones
WHERE (opened_day <= reviewed_day AND reviewed_day < closed_day AND closed_day <= opened_day) OR urgent = 1;

-- Smaller reference (hide during generation)
SELECT row_id
FROM milestones
WHERE urgent = 1;

ROLLBACK;

-- E. Simplification under schema constraints

-- E1. Schema guarantees a bound
-- Schema example | implied condition.
-- Jarke, External Semantic Query Simplification, section 2 on reasoning from integrity constraints.
-- https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf
-- Brass and Goldberg, Semantic Errors in SQL Queries, errors 1 and 8.
-- https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf
-- Every legal product has nonnegative stock.
-- Without the CHECK, stock = -1, price_cents = 501 is legal. Original keeps row: False.
-- Reference keeps row: True.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 2, 3.
BEGIN TRANSACTION;
CREATE TABLE products (
    row_id BIGINT NOT NULL,
    stock BIGINT NOT NULL,
    price_cents BIGINT NOT NULL,
    CHECK (stock >= 0)
);
INSERT INTO products VALUES
    (1, 0, 500),
    (2, 0, 501),
    (3, 4, 700),
    (4, 3, 499);

-- Original
SELECT row_id
FROM products
WHERE stock >= 0 AND price_cents > 500;

-- Smaller reference (hide during generation)
SELECT row_id
FROM products
WHERE price_cents > 500;

ROLLBACK;

-- E2. Column ordering transfers a bound
-- Schema example | jobs example from the project proposal, supported by constraint-based
-- simplification.
-- Jarke, External Semantic Query Simplification, section 2 on reasoning from integrity constraints.
-- https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf
-- A job starting on or after day 10 must also end on or after day 10.
-- Without the CHECK, start_day = 10, end_day = 9 is legal. Original keeps row: False. Reference
-- keeps row: True.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 3, 4, 5.
BEGIN TRANSACTION;
CREATE TABLE jobs (
    row_id BIGINT NOT NULL,
    start_day BIGINT NOT NULL,
    end_day BIGINT NOT NULL,
    CHECK (start_day <= end_day)
);
INSERT INTO jobs VALUES
    (1, 9, 9),
    (2, 9, 10),
    (3, 10, 10),
    (4, 10, 12),
    (5, 11, 12);

-- Original
SELECT row_id
FROM jobs
WHERE start_day >= 10 AND end_day >= 10;

-- Smaller reference (hide during generation)
SELECT row_id
FROM jobs
WHERE start_day >= 10;

ROLLBACK;

-- E3. Equal columns can be substituted
-- Schema example | substitution using a declared equality.
-- Jarke, External Semantic Query Simplification, section 2 on reasoning from integrity constraints.
-- https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf
-- The billed and posted amounts must coincide.
-- Without the CHECK, billed_cents = 50, posted_cents = 51 is legal. Original keeps row: False.
-- Reference keeps row: True.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 1, 2.
BEGIN TRANSACTION;
CREATE TABLE invoices (
    row_id BIGINT NOT NULL,
    billed_cents BIGINT NOT NULL,
    posted_cents BIGINT NOT NULL,
    CHECK (billed_cents = posted_cents)
);
INSERT INTO invoices VALUES
    (1, 49, 49),
    (2, 50, 50),
    (3, 51, 51);

-- Original
SELECT row_id
FROM invoices
WHERE billed_cents < 50 OR posted_cents = 50;

-- Smaller reference (hide during generation)
SELECT row_id
FROM invoices
WHERE billed_cents <= 50;

ROLLBACK;

-- E4. Finite domain has a shorter complement
-- Schema example | complement within an enumerated legal domain.
-- Jarke, External Semantic Query Simplification, section 2 on reasoning from integrity constraints.
-- https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf
-- Among the three legal codes, excluding 20 selects exactly 10 and 30.
-- Without the CHECK, status_code = 40 is legal. Original keeps row: False. Reference keeps row:
-- True.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 1, 3, 4.
BEGIN TRANSACTION;
CREATE TABLE orders (
    row_id BIGINT NOT NULL,
    status_code BIGINT NOT NULL,
    CHECK (status_code = 10 OR status_code = 20 OR status_code = 30)
);
INSERT INTO orders VALUES
    (1, 10),
    (2, 20),
    (3, 30),
    (4, 30);

-- Original
SELECT row_id
FROM orders
WHERE status_code = 10 OR status_code = 30;

-- Smaller reference (hide during generation)
SELECT row_id
FROM orders
WHERE status_code != 20;

ROLLBACK;

-- E5. Conditional rule implies a filter condition
-- Schema example | implication from a row-local conditional rule.
-- Jarke, External Semantic Query Simplification, section 2 on reasoning from integrity constraints.
-- https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf
-- A negative amount is allowed only for kinds other than 1.
-- Without the CHECK, entry_kind = 1, amount_cents = -5 is legal. Original keeps row: False.
-- Reference keeps row: True.
-- Predicate AST nodes: 7 -> 3. Expected row IDs from each SELECT: 4, 5.
BEGIN TRANSACTION;
CREATE TABLE entries (
    row_id BIGINT NOT NULL,
    entry_kind BIGINT NOT NULL,
    amount_cents BIGINT NOT NULL,
    CHECK (entry_kind != 1 OR amount_cents >= 0)
);
INSERT INTO entries VALUES
    (1, 1, 0),
    (2, 1, 10),
    (3, 2, 0),
    (4, 2, -5),
    (5, 3, -1);

-- Original
SELECT row_id
FROM entries
WHERE entry_kind != 1 AND amount_cents < 0;

-- Smaller reference (hide during generation)
SELECT row_id
FROM entries
WHERE amount_cents < 0;

ROLLBACK;
