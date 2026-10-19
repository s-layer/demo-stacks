"""Rendered DDL for each warehouse, and the committed generated/ directory."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

import yaml

from demo_stacks import cli
from demo_stacks.render import databricks, semolina, snowflake
from demo_stacks.render import duckdb as render_duckdb

if TYPE_CHECKING:
    from pathlib import Path

    from demo_stacks import semantic


def test_generated_directory_is_up_to_date(defs: semantic.Definitions) -> None:
    for path, text in cli.rendered_files(defs).items():
        on_disk = cli.GENERATED / path
        assert on_disk.exists(), f"{path} missing; run `demo-stacks render`"
        assert on_disk.read_text() == text, f"{path} is stale; run `demo-stacks render`"


def test_render_check_flags_a_stale_file(tmp_path: Path, defs: semantic.Definitions) -> None:
    assert cli.main(["render", "--out", str(tmp_path)]) == 0
    assert cli.main(["render", "--out", str(tmp_path), "--check"]) == 0
    (tmp_path / "duckdb" / "tpch_orders.sql").write_text("-- edited\n")
    assert cli.main(["render", "--out", str(tmp_path), "--check"]) == 1


class TestDuckDB:
    def test_tables_are_qualified_by_schema(self, defs: semantic.Definitions) -> None:
        ddl = render_duckdb.render(defs.view("tpch_orders"), schema="s3t.tpch")
        assert "o AS s3t.tpch.orders PRIMARY KEY (o_orderkey)" in ddl
        assert render_duckdb.render(defs.view("tpch_orders")).count("o AS orders ") == 1

    def test_derived_metrics_stay_bare(self, defs: semantic.Definitions) -> None:
        ddl = render_duckdb.render(defs.view("tpch_sales"))
        assert "    profit AS net_revenue - supply_cost COMMENT" in ddl

    def test_view_comment_is_a_separate_statement(self, defs: semantic.Definitions) -> None:
        stmts = render_duckdb.statements(defs.view("tpch_orders"))
        assert stmts[0].startswith("CREATE OR REPLACE SEMANTIC VIEW tpch_orders AS\nTABLES (")
        assert stmts[1].startswith("ALTER SEMANTIC VIEW tpch_orders SET COMMENT = ")
        assert len(stmts) == 2


class TestSnowflake:
    def test_no_as_and_inline_comment(self, defs: semantic.Definitions) -> None:
        ddl = snowflake.render(defs.view("tpch_orders"), schema="DB.TPCH")
        assert ddl.startswith("CREATE OR REPLACE SEMANTIC VIEW tpch_orders\nTABLES (")
        assert "ALTER" not in ddl
        assert ddl.rstrip().endswith(
            "COMMENT = 'Orders and order value by customer and geography.';"
        )

    def test_derived_metrics_reference_qualified_metrics(self, defs: semantic.Definitions) -> None:
        ddl = snowflake.render(defs.view("tpch_sales"))
        assert "    profit AS li.net_revenue - li.supply_cost COMMENT" in ddl
        ddl = snowflake.render(defs.view("tpch_orders"))
        assert "    orders_per_customer AS o.order_count / o.customer_count" in ddl

    def test_quotes_in_comments_are_escaped(self, defs: semantic.Definitions) -> None:
        assert "COMMENT = 'The customer''s nation.'" in snowflake.render(defs.view("tpch_sales"))


class TestDatabricks:
    def body(self, ddl: str) -> Any:
        head, _, rest = ddl.partition("AS $$\n")
        assert head.endswith("WITH METRICS\nLANGUAGE YAML\n")
        return yaml.safe_load(rest.removesuffix("$$;\n"))

    def test_joins_nest_along_relationships(self, defs: semantic.Definitions) -> None:
        body = self.body(databricks.render(defs.view("tpch_sales"), schema="cat.tpch"))
        assert body["version"] == 1.1
        assert body["source"] == "cat.tpch.lineitem"
        joins = {j["name"]: j for j in body["joins"]}
        assert set(joins) == {"o", "p", "s", "ps"}
        assert joins["ps"]["on"] == (
            "source.l_partkey = ps.ps_partkey AND source.l_suppkey = ps.ps_suppkey"
        )
        c = joins["o"]["joins"][0]
        assert (c["name"], c["source"], c["on"]) == (
            "c",
            "cat.tpch.customer",
            "o.o_custkey = c.c_custkey",
        )
        cr = c["joins"][0]["joins"][0]
        assert (cr["name"], cr["on"]) == ("cr", "cn.n_regionkey = cr.r_regionkey")

    def test_expressions_use_source_and_join_paths(self, defs: semantic.Definitions) -> None:
        body = self.body(databricks.render(defs.view("tpch_sales")))
        dims = {d["name"]: d["expr"] for d in body["dimensions"]}
        assert dims["ship_mode"] == "source.l_shipmode"
        assert dims["market_segment"] == "o.c.c_mktsegment"
        assert dims["customer_region"] == "o.c.cn.cr.r_name"
        assert dims["supplier_region"] == "s.sn.sr.r_name"

    def test_facts_are_inlined_and_derived_metrics_use_measure(
        self, defs: semantic.Definitions
    ) -> None:
        body = self.body(databricks.render(defs.view("tpch_sales")))
        measures = {m["name"]: m["expr"] for m in body["measures"]}
        assert measures["supply_cost"] == "sum((ps.ps_supplycost) * source.l_quantity)"
        assert measures["profit"] == "MEASURE(net_revenue) - MEASURE(supply_cost)"
        assert measures["line_count"] == "count(*)"


class TestSemolina:
    def test_module_declares_every_field(self, defs: semantic.Definitions) -> None:
        source = semolina.render(defs)
        tree = ast.parse(source)
        classes = {c.name: c for c in tree.body if isinstance(c, ast.ClassDef)}
        assert set(classes) == {"TpchSales", "TpchOrders"}
        for view in defs.views:
            cls = classes[semolina.class_name(view)]
            fields: dict[str, str] = {}
            for stmt in cls.body:
                if isinstance(stmt, ast.Assign):
                    target, call = stmt.targets[0], stmt.value
                    assert isinstance(target, ast.Name)
                    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    fields[target.id] = call.func.id
            want = (
                {f.name: "Fact" for f in view.facts}
                | {d.name: "Dimension" for d in view.dimensions}
                | {m.name: "Metric" for m in view.metrics}
            )
            assert fields == want
