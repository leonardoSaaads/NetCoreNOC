"""The NOC's customers and their books (ADR #473).

* a customer's sheet of circuits — its header rows of sites and segments over Shelf/Slot/Port
  columns — reads into circuits with their fields, attributes and path hop by hop, from pasted
  text or an Excel workbook; a workbook part with a DTD is refused;
* a document is kept only when its first bytes are what its name says, its text is read for the
  agents, and it is downloaded as an attachment that runs nothing;
* the instructions, the workbook, the documents and the circuits are kept by people; an agent
  keeps the memory and a circuit's notes — every write an audit row under its writer;
* a customer is visible when one of its elements is in the caller's scope, and one that is not
  answers as if it did not exist; a viewer is not shown a contact's email or phone;
* one route reads more than the 1 MiB every other route is held to, and only after it has
  authenticated the caller.
"""

from __future__ import annotations

import io
import json
import zipfile
import zlib
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pytest

from netcorenoc.api import mcp_tools
from netcorenoc.engine.noc import circuits, doctext, office
from netcorenoc.store import Store

import authutil

SHEET = (Path(__file__).parents[1] / "fixtures" / "noc_circuits.tsv").read_text()


def _xlsx(rows: list[list[str]], *, doctype: bool = False) -> bytes:
    """A minimal workbook, as Excel writes one: shared strings, one sheet."""
    strings: list[str] = []
    cells = []
    for r, row in enumerate(rows, start=1):
        out = []
        for c, value in enumerate(row):
            if value == "":
                continue
            strings.append(value)
            ref = f"{chr(65 + c % 26) if c < 26 else 'A' + chr(65 + c - 26)}{r}"
            out.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        cells.append(f'<row r="{r}">{"".join(out)}</row>')
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    dtd = '<!DOCTYPE x [<!ENTITY a "aaaa">]>' if doctype else ""
    shared = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
            '<sheet name="Circuits" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr("xl/sharedStrings.xml", f'{dtd}<sst xmlns="{main}">{shared}</sst>')
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{main}"><sheetData>{"".join(cells)}</sheetData></worksheet>',
        )
    return buffer.getvalue()


def _pdf(text: str) -> bytes:
    stream = zlib.compress(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    return (
        b"%PDF-1.4\n1 0 obj << /Length "
        + str(len(stream)).encode()
        + b" /Filter /FlateDecode >>\nstream\n"
        + stream
        + b"\nendstream\nendobj\n%%EOF\n"
    )


def test_the_customers_sheet_reads_into_circuits_with_their_path() -> None:
    found = circuits.parse_table(circuits.read_text_table(SHEET))
    assert [c["designation"] for c in found] == [
        "SILLA-TO-ALZUR 2- ETH 10G-P02-002",
        "SILLA-TO-QATAR 1-ETH 10G-P01-001",
        "SILLA-TO-JASRA-01-ETH-10G -P005-004",
        "SILLA-TO-QATAR-15-ETH 100G-P002-002",
        "SILLA-TO-JASRA-01-ETH-10G -P006-005",
    ]
    first = found[0]
    assert (first["a_end"], first["z_end"], first["bandwidth"]) == ("IMPZ", "MOC/Kuwait", "10G")
    assert (first["status"], first["owner"], first["provider"]) == ("Active", "Zajil", "")
    assert first["attributes"]["Du Ref ID"] == "DTM-00066"
    hops = [circuits.hop_text(h) for h in first["path"]]
    assert hops[0] == (
        "SILA PoP · MEETS ODF: Backhaul/Front ODF1 (1 Tx & 2Rx), Internal/Rear ODF1 (1 Tx & 2Rx)"
    )
    assert hops[1] == "SILA PoP · Cient Port: Shelf 1, Slot 9, Port 1, Mode 10G"
    assert hops[-2] == "AL ZOUR PoP · Cient Port: Shelf 1, Slot 13, Port 2, Mode 10G"
    # The du circuit names its front-ending party, and a cell with a line break stays one value.
    du = found[3]
    assert du["provider"] == "du"
    assert "\n" in du["attributes"]["Du OTN IMPZ /Kalba"]
    # Nothing "NA" survives as a value.
    assert all("NA" not in h["fields"].values() for c in found for h in c["path"])


def test_a_workbook_reads_as_its_rows_and_one_with_a_dtd_is_refused() -> None:
    rows = circuits.read_text_table(SHEET)
    data = _xlsx([[c.strip() for c in r[:40]] for r in rows])
    from_excel = circuits.parse_table(office.xlsx_rows(data))
    assert [c["designation"] for c in from_excel][:2] == [
        "SILLA-TO-ALZUR 2- ETH 10G-P02-002",
        "SILLA-TO-QATAR 1-ETH 10G-P01-001",
    ]
    assert "IMPZ" in office.xlsx_text(data)
    with pytest.raises(office.OfficeError, match="DTD"):
        office.xlsx_rows(_xlsx([["Circuit"], ["x"]], doctype=True))
    with pytest.raises(circuits.CircuitError, match="header"):
        circuits.parse_table([["a", "b"], ["1", "2"]])


def test_a_document_is_what_its_first_bytes_say() -> None:
    assert doctext.classify("topology.pdf", _pdf("Rack R1C3")).ext == "pdf"
    with pytest.raises(doctext.DocError, match="does not start"):
        doctext.classify("topology.pdf", b"MZ\x90\x00 an executable")
    with pytest.raises(doctext.DocError, match="type"):
        doctext.classify("tool.exe", b"MZ")
    with pytest.raises(doctext.DocError, match="name"):
        doctext.classify("../etc/passwd.txt", b"x")
    kind = doctext.classify("topology.pdf", _pdf("Shelf 1 at SILA PoP, row 8 cab 6"))
    text, _note = doctext.extract(kind, _pdf("Shelf 1 at SILA PoP, row 8 cab 6"))
    assert "Shelf 1 at SILA PoP, row 8 cab 6" in text


async def _estate(store: Store) -> dict[str, int]:
    """Two customers, each with one element: Akamai on 10.0.0.1, TIM on 192.168.50.1."""
    async with store.lock:
        ids = {}
        estate = (("Akamai", "akamai", "10.0.0.1"), ("TIM Brasil", "tim", "192.168.50.1"))
        for name, slug, ip in estate:
            ne = await store.ne_id(ip, 1.0)
            org = await store.create_organization(name, slug, 1.0)
            await store.set_ne_organization(ne, org)
            ids[slug] = org
        await store.commit()
    return ids


async def _agent(store: Store) -> str:
    """A token the NOC hired as an agent (its grant), working as an editor."""
    token = await authutil.make_token(store, "vega", "editor")
    async with store.lock:
        cur = await store.conn.execute("SELECT id FROM api_token WHERE name = 'vega'")
        row = await cur.fetchone()
        assert row is not None
        token_id = int(row[0])
        await store.noc_set_grant(f"token:{token_id}", "agent", True, 0.0, "test")
        await store.commit()
    return token


async def _audit(store: Store, action: str) -> list[dict[str, Any]]:
    async with store.reading():
        cur = await store.conn.execute(
            "SELECT actor, object_id, details FROM audit_log WHERE action = ? ORDER BY id",
            (action,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def test_a_customers_book_is_kept_by_people_and_its_memory_by_agents(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _estate(store)
    akamai = ids["akamai"]
    token = await _agent(store)
    editor = await authutil.client_as(app, "editor")
    viewer = await authutil.client_as(app, "viewer")
    agent = authutil.new_client(app)
    agent.headers["authorization"] = f"Bearer {token}"
    base = f"/api/noc/customers/{akamai}"
    try:
        listed = (await editor.get("/api/noc/customers")).json()
        assert [c["name"] for c in listed["customers"]] == ["Akamai", "TIM Brasil"]

        said = "Name every case 'AKAM-<site>-<fault>'. Call Zayo first for any fibre cut."
        resp = await editor.post(base, json={"instructions": said, "version": 0})
        assert resp.status_code == 200, resp.text
        assert resp.json()["instructions"] == said
        stale = await editor.post(base, json={"instructions": "x", "version": 0})
        assert stale.status_code == 409
        assert (await viewer.post(base, json={"instructions": "x"})).status_code == 403

        for level, name in ((1, "Zayo NOC"), (2, "Zayo duty manager")):
            resp = await editor.post(
                f"{base}/contacts",
                json={
                    "party": "Zayo",
                    "party_kind": "provider",
                    "escalation_level": level,
                    "name": name,
                    "email": "noc@zayo.example",
                    "phone": "+1 800 555 0100",
                    "hours": "24x7",
                },
            )
            assert resp.status_code == 200, resp.text
        bad = await editor.post(
            f"{base}/contacts", json={"party": "Akamai", "name": "Ops", "email": "nope"}
        )
        assert bad.status_code == 400 and "email" in bad.json()["detail"]

        book = (await editor.get(base)).json()
        zayo = book["workbook"][0]
        assert zayo["party"] == "Zayo"
        assert [c["name"] for c in zayo["contacts"]] == ["Zayo NOC", "Zayo duty manager"]
        assert zayo["contacts"][0]["email"] == "noc@zayo.example"
        seen = (await viewer.get(base)).json()["workbook"][0]["contacts"][0]
        assert seen["email"] == "hidden for your role" and seen["phone"] == "hidden for your role"

        # The circuits: a preview stores nothing, then the sheet is stored.
        preview = await editor.post(f"{base}/circuits/import", json={"text": SHEET})
        assert preview.json()["found"] == 5 and preview.json()["applied"] is False
        assert (await editor.get(f"{base}/circuits")).json()["total"] == 0
        applied = await editor.post(f"{base}/circuits/import", json={"text": SHEET, "apply": True})
        assert applied.json()["applied"] is True and applied.json()["new"] == 5
        found = (await agent.get("/api/noc/circuits", params={"q": "dtm-00066"})).json()
        names = [c["designation"] for c in found["circuits"]]
        assert names == ["SILLA-TO-ALZUR 2- ETH 10G-P02-002"]
        circuit = found["circuits"][0]["id"]
        whole = (await agent.get(f"{base}/circuits/{circuit}")).json()
        assert whole["path_text"][1] == "SILA PoP · Cient Port: Shelf 1, Slot 9, Port 1, Mode 10G"

        # An agent keeps the memory and a circuit's notes — and nothing the customer said.
        resp = await agent.post(
            f"{base}/memory", json={"memory": "Zayo answers only by phone after 22:00 UTC."}
        )
        assert resp.status_code == 200, resp.text
        assert "(vega): Zayo answers only by phone" in resp.json()["memory"]
        # The memory moves on its own version: a person's edit read before it is not stale.
        kept = await editor.post(base, json={"instructions": said, "version": 1})
        assert kept.status_code == 200, kept.text
        refused = await agent.post(base, json={"instructions": "be quick"})
        assert refused.status_code == 403 and "kept by people" in refused.json()["detail"]
        assert (
            await agent.post(f"{base}/contacts", json={"party": "x", "name": "y", "phone": "123"})
        ).status_code == 403
        noted = await agent.post(
            f"{base}/circuits/{circuit}/notes", json={"notes": "Patch cord at ODF1 1Tx was dirty."}
        )
        assert noted.status_code == 200 and "dirty" in noted.json()["notes"]
        found = (await agent.get("/api/noc/circuits", params={"q": "dirty"})).json()
        assert found["total"] == 1

        # A re-import replaces the circuit and keeps its notes.
        again = await editor.post(f"{base}/circuits/import", json={"text": SHEET, "apply": True})
        assert again.json()["replaced"] == 5
        assert "dirty" in (await editor.get(f"{base}/circuits/{circuit}")).json()["notes"]

        # Documents: checked by their bytes, read as text, downloaded as an attachment.
        pdf = _pdf("SILA PoP shelf 1 is in row 8, cabinet 6")
        up = await editor.post(
            f"{base}/docs",
            params={"name": "SILA layout.pdf", "title": "SILA PoP layout", "category": "site"},
            content=pdf,
        )
        assert up.status_code == 200, up.text
        doc = up.json()["id"]
        assert up.json()["type_label"] == "PDF" and up.json()["text_chars"] > 0
        exe = await editor.post(f"{base}/docs", params={"name": "x.pdf"}, content=b"MZ\x90\x00")
        assert exe.status_code == 400
        assert (
            await agent.post(f"{base}/docs", params={"name": "a.txt"}, content=b"hello")
        ).status_code == 403
        read = (await agent.get(f"{base}/docs/{doc}")).json()
        assert "row 8, cabinet 6" in read["text"] and read["next_offset"] is None
        file = await viewer.get(f"{base}/docs/{doc}/file")
        assert file.content == pdf
        assert file.headers["content-disposition"].startswith("attachment;")
        assert "sandbox" in file.headers["content-security-policy"]

        # A document over the 1 MiB every other route takes, as its body.
        big = b"circuit notes\n" * 100_000
        assert len(big) > 1024 * 1024
        up = await editor.post(f"{base}/docs", params={"name": "big.txt"}, content=big)
        assert up.status_code == 200, up.text
        assert (await editor.post(f"{base}/circuits/import", content=big)).status_code == 413
        anonymous = authutil.new_client(app)
        assert (
            await anonymous.post(f"{base}/docs", params={"name": "a.txt"}, content=big)
        ).status_code == 401
        await anonymous.aclose()

        assert (await editor.post(f"{base}/docs/{doc}/delete")).status_code == 200
        assert (await editor.get(f"{base}/docs/{doc}")).status_code == 404
    finally:
        for client in (editor, viewer, agent):
            await client.aclose()

    assert {r["actor"] for r in await _audit(store, "noc.customer.update")} == {"edt"}
    assert [r["actor"] for r in await _audit(store, "noc.customer.memory")] == ["vega"]
    assert [r["actor"] for r in await _audit(store, "noc.circuit.notes")] == ["vega"]
    assert len(await _audit(store, "noc.contact.create")) == 2
    assert len(await _audit(store, "noc.circuit.import")) == 2
    uploads = await _audit(store, "noc.doc.upload")
    assert json.loads(uploads[0]["details"])["sha256"]


async def test_a_customer_outside_the_callers_scope_does_not_exist(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _estate(store)
    admin = await authutil.client_as(app, "admin")
    tim = f"/api/noc/customers/{ids['tim']}"
    try:
        stored = await admin.post(f"{tim}/circuits/import", json={"text": SHEET, "apply": True})
        assert stored.status_code == 200, stored.text
    finally:
        await admin.aclose()
    await authutil.set_scope(store, {"roles": {"viewer": ["10.0.0.0/8"], "editor": ["10.0.0.0/8"]}})
    editor = await authutil.client_as(app, "editor")
    try:
        listed = (await editor.get("/api/noc/customers")).json()
        assert [c["name"] for c in listed["customers"]] == ["Akamai"]
        missing = await editor.get("/api/noc/customers/999999")
        hidden = await editor.get(tim)
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()
        assert (await editor.get("/api/noc/circuits", params={"q": "silla"})).json()["total"] == 0
        assert (await editor.post(f"{tim}/memory", json={"memory": "x"})).status_code == 404
    finally:
        await editor.aclose()


async def test_the_agents_tools_read_the_book_and_keep_its_memory(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    tools = mcp_tools.build(app.openapi())  # type: ignore[attr-defined]
    for name in (
        "noc_list_customers",
        "noc_get_customer",
        "noc_update_customer_memory",
        "noc_read_customer_doc",
        "noc_find_circuits",
        "noc_list_circuits",
        "noc_get_circuit",
        "noc_note_circuit",
    ):
        assert name in tools, name
    assert tools["noc_read_customer_doc"].untrusted
    assert set(tools["noc_update_customer_memory"].schema["properties"]) >= {
        "customer_id",
        "memory",
        "mode",
    }
    assert "noc_set_customer_instructions" not in tools
