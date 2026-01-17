# app.py
import streamlit as st
import pandas as pd
from barcode import Code128
from barcode.writer import ImageWriter
from io import BytesIO
import base64
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import os
import tempfile
import contextlib
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timedelta

# -----------------------------
# Load environment variables
# -----------------------------
load_dotenv(".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Missing SUPABASE_URL or SUPABASE_KEY in your .env file.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------------
# Streamlit config
# -----------------------------
st.set_page_config(page_title="Barcode Inventory App", layout="centered")
st.title("Barcode Inventory Management")

# -----------------------------
# Initialize session state
# -----------------------------
defaults = {
    "truck_logged_in": False,
    "truck_username": "",
    "truck_role": "",
    "admin_logged_in": False,
    "admin_username": "",
    "pending_add": None,
    "last_barcode_b64": None,
    "last_barcode_label": None,
    "last_barcode_bytes": None,
    "pending_delete_user": None,
    "user_scan_input": "",
    "user_mode_scan_data": None,
    "manual_update_visible": False,
    "update_success": None,
    "last_processed_scan": "",
    "clear_scan_box": False,
    "manual_update_done": False,
    "confirm_clear_inventory": False,
    "confirm_delete_truck": None,
    "current_truck_id": None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# -----------------------------
# Slot assignment cache (per session)
# -----------------------------
if "batch_assigned_slots" not in st.session_state:
    st.session_state.batch_assigned_slots = {}
batch_assigned_slots = st.session_state.batch_assigned_slots


# -----------------------------
# Helper functions
# -----------------------------
def move_depleted_to_history():
    """Move items depleted > 24 hours ago from inventory to history, then delete them from inventory."""
    one_day_ago = datetime.utcnow() - timedelta(days=1)

    response = (
        supabase.from_("inventory")
        .select(
            "id, item_code, slot, status, depleted_at, added_by, added_at, truck_id, in_stock_at, in_use_at"
        )
        .lte("depleted_at", one_day_ago.isoformat())
        .execute()
    )

    if not response.data:
        return

    # Grab IDs BEFORE mutating rows
    ids_to_delete = [row["id"] for row in response.data if row.get("id") is not None]

    # Copy rows for history insert (remove id)
    history_rows = []
    for row in response.data:
        r = dict(row)
        r.pop("id", None)
        history_rows.append(r)

    # Insert into history
    supabase.from_("history").insert(history_rows).execute()

    # Delete originals
    if ids_to_delete:
        supabase.from_("inventory").delete().in_("id", ids_to_delete).execute()


def create_barcode_pdf(barcodes, skip_slots=0):
    """
    Create a 10x3 sticker sheet PDF.
    barcodes: list of tuples (label, png_bytes) - png_bytes is not used here (we regenerate cleanly).
    skip_slots: how many label positions to skip (for partially used sheets).
    """
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=letter)
    page_w, page_h = letter

    # Layout: 3 columns × 10 rows
    margin_x = 36
    col_spacing = 20
    row_spacing = 15
    cols = 3
    rows = 10

    sticker_w = (page_w - 2 * margin_x - (cols - 1) * col_spacing) / cols
    sticker_h = 60

    grid_height = rows * sticker_h + (rows - 1) * row_spacing
    margin_y = (page_h - grid_height) / 2

    col = skip_slots % cols
    row = skip_slots // cols
    temp_files = []

    try:
        for label, _ in barcodes:
            barcode_obj = Code128(label, writer=ImageWriter())
            options = {"module_width": 0.35, "module_height": 18, "write_text": False}

            barcode_bytes = BytesIO()
            barcode_obj.write(barcode_bytes, options)

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_img:
                temp_img.write(barcode_bytes.getvalue())
                temp_filepath = temp_img.name
                temp_files.append(temp_filepath)

            x_pos = margin_x + col * (sticker_w + col_spacing)
            y_pos = page_h - margin_y - (row + 1) * sticker_h - row * row_spacing

            c.drawImage(
                temp_filepath,
                x_pos,
                y_pos + 12,
                width=sticker_w,
                height=sticker_h - 20,
                preserveAspectRatio=True,
                anchor="n",
            )

            c.setFont("Helvetica-Bold", 10)
            c.drawCentredString(x_pos + sticker_w / 2, y_pos, label)

            col += 1
            if col >= cols:
                col = 0
                row += 1
                if row >= rows:
                    c.showPage()
                    row = 0

        c.save()
        pdf_buffer.seek(0)
        return pdf_buffer.getvalue()

    finally:
        for f in temp_files:
            with contextlib.suppress(OSError):
                os.remove(f)


def ensure_default_admin():
    response = supabase.table("users").select("*").execute()
    if response.data is None or len(response.data) == 0:
        supabase.table("users").insert(
            {"username": "Lauren", "password": "952426", "role": "admin"}
        ).execute()


def generate_barcode_bytes(label_text: str) -> bytes:
    buf = BytesIO()
    Code128(label_text, writer=ImageWriter()).write(buf, options={"write_text": True})
    buf.seek(0)
    return buf.getvalue()


def show_last_barcode():
    if st.session_state.last_barcode_b64:
        st.subheader("Last Generated Barcode")
        st.image(
            st.session_state.last_barcode_bytes,
            caption=st.session_state.last_barcode_label,
            width=300,
        )
        st.download_button(
            label="Download & Print Barcode",
            data=st.session_state.last_barcode_bytes,
            file_name=f"{st.session_state.last_barcode_label}.png",
            mime="image/png",
        )
        st.info(
            "The last generated barcode is saved here until a new one is created. "
            "Click 'Download' to save the image to your computer, then print it."
        )


def check_login(username, password_input):
    try:
        user_data = (
            supabase.from_("users")
            .select("password, role")
            .eq("username", username)
            .single()
            .execute()
            .data
        )
        if user_data:
            stored_password = user_data["password"]
            role = user_data["role"]
            if password_input == stored_password:
                return True, role
    except Exception:
        return False, None
    return False, None


def close_truck(truck_id, closed_by):
    now = datetime.utcnow().isoformat()
    supabase.from_("anticipated_trucks").update({"status": "closed"}).eq("id", truck_id).execute()
    supabase.from_("analytics_history").insert(
        {"truck_id": truck_id, "closed_by": closed_by, "closed_at": now}
    ).execute()
    st.success(f"Truck {truck_id} closed by {closed_by} at {now}.")


def get_next_slot(item_code):
    if item_code not in batch_assigned_slots:
        batch_assigned_slots[item_code] = set()

    inventory_items = (
        supabase.from_("inventory")
        .select("slot, status")
        .eq("item_code", item_code)
        .execute()
        .data
    )

    # Only reserve slots for PENDING anticipated items (prevents old trucks from blocking slots forever)
    anticipated_items = (
        supabase.from_("anticipated_items")
        .select("slot")
        .eq("item_code", item_code)
        .eq("status", "pending")
        .execute()
        .data
    )

    used_slots = (
        {int(item["slot"]) for item in inventory_items if item.get("slot")}
        | {int(item["slot"]) for item in anticipated_items if item.get("slot")}
        | batch_assigned_slots[item_code]
    )

    all_slots = set(range(1, 100))
    available_slots = sorted(all_slots - used_slots)

    next_slot = available_slots[0] if available_slots else 1
    batch_assigned_slots[item_code].add(next_slot)
    return next_slot


def handle_user_scan_auto():
    scanned_code = st.session_state.user_scan_input

    st.session_state.user_mode_scan_data = None
    st.session_state.manual_update_visible = False
    st.session_state.update_success = None
    st.session_state.last_processed_scan = scanned_code

    if not scanned_code:
        st.error("Please scan or enter a barcode.")
        return

    try:
        parts = scanned_code.strip().rsplit("_", 1)
        if len(parts) != 2:
            st.error("Invalid format. Use `itemcode_slot` (e.g., `CFA_SAUCE_1`).")
            return

        item_code, slot_s = parts
        slot = int(slot_s)

        allowed_item = (
            supabase.from_("allowed_items")
            .select("item_name")
            .eq("item_name", item_code)
            .execute()
            .data
        )
        if not allowed_item:
            st.error("NOT REGISTERED: This item code is not in the allowed list.")
            return

        inventory_item = (
            supabase.from_("inventory")
            .select("status")
            .eq("item_code", item_code)
            .eq("slot", slot)
            .execute()
            .data
        )
        if not inventory_item:
            st.error("Item not found in inventory. Please check the barcode or add it first.")
            return

        current_status = inventory_item[0]["status"]
        st.session_state.user_mode_scan_data = {
            "item_code": item_code,
            "slot": slot,
            "current_status": current_status,
        }
        st.success(
            f"Scanned: **{item_code}**, Slot **{slot}**. Current Status: **{current_status}**"
        )

        if current_status == "in_stock":
            oldest_item = (
                supabase.from_("inventory")
                .select("slot")
                .eq("item_code", item_code)
                .eq("status", "in_stock")
                .order("added_at")
                .limit(1)
                .execute()
                .data
            )
            if oldest_item:
                oldest_slot = oldest_item[0]["slot"]
                if oldest_slot == slot:
                    st.markdown(
                        '<div style="background-color:#28a745;color:white;padding:10px;'
                        'border-radius:5px;text-align:center;">FIFO HINT: USE THIS ITEM FIRST</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="background-color:#dc3545;color:white;padding:10px;'
                        f'border-radius:5px;text-align:center;">FIFO HINT: '
                        f'**{item_code}_{oldest_slot}** is first</div>',
                        unsafe_allow_html=True,
                    )

    except (ValueError, IndexError):
        st.error("Invalid format. Use `itemcode_slot` (e.g., `CFA_SAUCE_1`).")


# -----------------------------
# User Mode Helpers
# -----------------------------
def process_scan_and_update(new_status, item_code, slot):
    now = datetime.utcnow().isoformat()
    update_data = {"status": new_status}

    if new_status == "in_use":
        update_data["in_use_at"] = now
    elif new_status == "depleted":
        update_data["depleted_at"] = now
    elif new_status == "in_stock":
        update_data["in_stock_at"] = now

    supabase.from_("inventory").update(update_data).eq("item_code", item_code).eq("slot", slot).execute()

    st.session_state.update_success = f"Item `{item_code}_{slot}` updated to **{new_status}**."
    reset_user_scan_state()


def reset_user_scan_state():
    st.session_state.user_mode_scan_data = None
    st.session_state.manual_update_visible = False


# -----------------------------
# Startup actions
# -----------------------------
ensure_default_admin()
move_depleted_to_history()


# -----------------------------
# Modes
# -----------------------------
def truck_mode():
    st.header("Truck Mode")

    if not st.session_state.get("truck_logged_in", False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.button("Login"):
            is_valid, role = check_login(username, password)
            if is_valid and role in ["truck", "admin"]:
                st.session_state.truck_logged_in = True
                st.session_state.truck_username = username
                st.session_state.truck_role = role
            else:
                st.error("Invalid credentials.")
        return

    st.write(f"Logged in as **{st.session_state.truck_username}** ({st.session_state.truck_role})")
    if st.button("Logout"):
        st.session_state.truck_logged_in = False
        st.session_state.truck_username = ""
        st.session_state.truck_role = ""
        return

    st.markdown("---")

    st.subheader("Select a Truck to Process")
    trucks_data = (
        supabase.from_("anticipated_trucks")
        .select("id, truck_name, created_at, status")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    trucks = pd.DataFrame(trucks_data)

    if trucks.empty:
        st.info("No trucks available. Please contact an admin to add one.")
        return

    truck_labels = trucks.apply(
        lambda r: f"ID {r['id']} - {r['truck_name']} ({str(r['created_at']).split('T')[0]})",
        axis=1,
    )
    t_choice = st.selectbox("Select a truck from the list:", truck_labels)
    t_id = int(t_choice.split(" - ")[0].replace("ID ", ""))

    st.session_state.current_truck_id = t_id
    selected_truck = trucks[trucks["id"] == t_id].iloc[0]
    st.success(f"Selected Truck: **{selected_truck['truck_name']} (ID {t_id})**")
    st.markdown("---")

    if str(selected_truck.get("status", "")).lower() == "closed":
        st.warning("This truck has been CLOSED. Scanning and adding items is disabled.")
        return

    st.subheader("Scan Barcode")
    with st.form("scan_form", clear_on_submit=True):
        scan = st.text_input("Scan or enter barcode:", key="scanner_input")
        submit_button = st.form_submit_button("Confirm Scan")

    if submit_button and scan:
        scan_data = (
            supabase.from_("anticipated_items")
            .select("id, item_code, slot, status")
            .eq("barcode_label", scan)
            .eq("status", "pending")
            .eq("truck_id", st.session_state.current_truck_id)
            .execute()
            .data
        )

        if scan_data:
            row = scan_data[0]
            aid, code, slot = row["id"], row["item_code"], row["slot"]
            now = datetime.utcnow().isoformat()

            try:
                supabase.from_("anticipated_items").update(
                    {"status": "scanned", "scanned_at": now}
                ).eq("id", aid).execute()

                supabase.from_("inventory").insert(
                    {
                        "item_code": code,
                        "slot": slot,
                        "status": "in_stock",
                        "added_by": st.session_state.truck_username,
                        "added_at": now,
                        "in_stock_at": now,
                        "truck_id": st.session_state.current_truck_id,
                    }
                ).execute()

                supabase.from_("anticipated_items").delete().eq("id", aid).execute()

                st.success(f"Barcode `{scan}` successfully received for truck {st.session_state.current_truck_id}.")
            except Exception as e:
                st.error(f"Error: An item with this barcode might already exist in inventory. Details: {e}")
        else:
            st.error(
                f"Barcode `{scan}` not found, not pending, or does not belong to truck {st.session_state.current_truck_id}."
            )

    st.markdown("---")

    st.subheader("Reprint Existing Barcode")
    in_stock_data = (
        supabase.from_("inventory")
        .select("item_code, slot, status")
        .eq("status", "in_stock")
        .execute()
        .data
    )
    df = pd.DataFrame(in_stock_data)

    if not df.empty:
        choices = df.apply(lambda r: f"{r['item_code']}_{r['slot']}", axis=1).tolist()
        choice = st.selectbox("Select item to reprint:", choices)
        if st.button("Reprint"):
            png = generate_barcode_bytes(choice)
            st.download_button("Download", png, file_name=f"{choice}.png", mime="image/png")
    else:
        st.info("No items in stock to reprint.")

    st.markdown("---")

    st.subheader("Emergency Add Item")
    allowed_data = supabase.from_("allowed_items").select("item_name").order("item_name").execute().data
    allowed = [item["item_name"] for item in allowed_data]

    if allowed:
        with st.form("emergency_add_form"):
            e_item = st.selectbox("Select item:", allowed)
            if st.form_submit_button("Add Emergency Item"):
                slot = get_next_slot(e_item)
                label = f"{e_item}_{slot}"
                now = datetime.utcnow().isoformat()
                try:
                    supabase.from_("inventory").insert(
                        {
                            "item_code": e_item,
                            "slot": slot,
                            "status": "in_stock",
                            "added_by": st.session_state.truck_username,
                            "added_at": now,
                            "in_stock_at": now,
                        }
                    ).execute()

                    st.success(f"Emergency added `{label}` to inventory.")

                    png = generate_barcode_bytes(label)
                    st.session_state.last_barcode_bytes = png
                    st.session_state.last_barcode_label = label
                    st.session_state.last_barcode_b64 = base64.b64encode(png).decode("utf-8")
                    st.rerun()

                except Exception as e:
                    st.error(f"Error adding item. This item-slot combination might already exist. Details: {e}")

        if st.session_state.get("last_barcode_b64"):
            show_last_barcode()
    else:
        st.warning("No allowed items are configured. Please contact an admin.")


def user_mode():
    st.header("User Mode - Update Item Status")

    if st.session_state.update_success:
        st.success(st.session_state.update_success)
        st.session_state.update_success = None

    # Clear logic
    if st.session_state.clear_scan_box:
        st.session_state.user_scan_input = ""
        st.session_state.clear_scan_box = False
        st.session_state.user_mode_scan_data = None

    st.text_input(
        "Scan or enter barcode (format: itemcode_slot)",
        key="user_scan_input",
        on_change=handle_user_scan_auto,
    )

    if st.button("Clear Box"):
        st.session_state.clear_scan_box = True
        st.rerun()

    scan_data = st.session_state.user_mode_scan_data
    if not scan_data:
        return

    item_code = scan_data["item_code"]
    slot = scan_data["slot"]
    current_status = scan_data["current_status"]

    st.info(f"Current status of **{item_code}_{slot}**: **{current_status}**")

    if current_status == "in_stock":
        st.button(
            "Mark as In Use",
            key=f"mark_in_use_{item_code}_{slot}",
            on_click=process_scan_and_update,
            args=("in_use", item_code, slot),
        )

    elif current_status == "in_use":
        st.warning(f"Next step: mark **{item_code}_{slot}** as depleted")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.button(
                "Confirm Depletion",
                key=f"confirm_depletion_{item_code}_{slot}",
                on_click=process_scan_and_update,
                args=("depleted", item_code, slot),
            )
        with col2:
            st.button("Cancel", key=f"cancel_in_use_{item_code}_{slot}", on_click=reset_user_scan_state)
        with col3:
            st.button(
                "Other Options",
                key=f"manual_override_{item_code}_{slot}",
                on_click=lambda: st.session_state.update({"manual_update_visible": True}),
            )

    elif current_status == "depleted":
        st.info("This item is already depleted.")
        col1, col2 = st.columns(2)
        with col1:
            st.button(
                "Mark as In Stock",
                key=f"mark_in_stock_{item_code}_{slot}",
                on_click=process_scan_and_update,
                args=("in_stock", item_code, slot),
            )
        with col2:
            st.button("Cancel", key=f"cancel_depleted_{item_code}_{slot}", on_click=reset_user_scan_state)

    if st.session_state.manual_update_visible:
        st.markdown("---")
        st.subheader("Manual Status Update")
        status_options = ["in_stock", "in_use", "depleted"]
        idx = status_options.index(current_status) if current_status in status_options else 0

        new_status_manual = st.radio(
            "Select new status:",
            status_options,
            index=idx,
            key=f"manual_status_radio_{item_code}_{slot}",
        )

        def confirm_manual_update():
            process_scan_and_update(new_status_manual, item_code, slot)
            st.session_state.manual_update_done = True
            st.session_state.manual_update_visible = False

        def cancel_manual_update():
            st.session_state.manual_update_visible = False

        col1, col2 = st.columns(2)
        with col1:
            st.button("Confirm Manual Update", key=f"confirm_manual_{item_code}_{slot}", on_click=confirm_manual_update)
        with col2:
            st.button("Cancel", key=f"cancel_manual_{item_code}_{slot}", on_click=cancel_manual_update)


def admin_mode():
    st.header("Admin Mode")

    if not st.session_state.admin_logged_in:
        username = st.text_input("Admin Username")
        password = st.text_input("Admin Password", type="password")
        if st.button("Login as Admin"):
            is_valid, role = check_login(username, password)
            if is_valid and role == "admin":
                st.session_state.admin_logged_in = True
                st.session_state.admin_username = username
                st.success("Admin logged in.")
            else:
                st.error("Invalid admin credentials.")
        return

    st.write(f"Logged in as **{st.session_state.admin_username}** (admin)")
    if st.button("Logout"):
        st.session_state.admin_logged_in = False
        st.session_state.admin_username = ""
        st.session_state.pending_delete_user = None
        return

    st.markdown("---")

    # -------- Product summary --------
    st.subheader("Product Summary")
    try:
        summary_data = supabase.from_("inventory").select("item_code, status, depleted_at").execute().data
        summary_df_raw = pd.DataFrame(summary_data)

        if not summary_df_raw.empty:
            summary_df_raw["depleted_at"] = pd.to_datetime(summary_df_raw["depleted_at"], errors="coerce", utc=True)

            summary_df = summary_df_raw.groupby("item_code")["status"].value_counts().unstack(fill_value=0)

            one_week_ago = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)
            depleted_this_week = (
                summary_df_raw[
                    (summary_df_raw["status"] == "depleted") & (summary_df_raw["depleted_at"] >= one_week_ago)
                ]
                .groupby("item_code")["status"]
                .count()
                .rename("Depleted This Week")
            )

            final_summary_df = (
                summary_df.reindex(columns=["in_stock", "in_use", "depleted"], fill_value=0)
                .rename(columns={"in_stock": "In Stock", "in_use": "In Use", "depleted": "Depleted Total"})
                .join(depleted_this_week, how="left")
                .fillna(0)
            )

            st.dataframe(final_summary_df)
        else:
            st.info("No inventory data to display.")
    except Exception as e:
        st.error(f"Error fetching product summary: {e}")

    # -------- Inventory summary + durations --------
    st.subheader("Inventory Overview")
    try:
        inventory_data = (
            supabase.from_("inventory")
            .select("item_code, slot, status, in_stock_at, in_use_at, depleted_at, added_at")
            .order("item_code")
            .order("slot")
            .execute()
            .data
        )

        df = pd.DataFrame(inventory_data)

        if not df.empty:
            # Force ALL timestamps to tz-aware UTC so subtraction works
            for col in ["in_stock_at", "in_use_at", "depleted_at", "added_at"]:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

            # Safe duration calculations (only compute when both timestamps exist)
            df["Days In Stock"] = (df["in_use_at"] - df["in_stock_at"]).dt.days.where(
                df["in_use_at"].notna() & df["in_stock_at"].notna()
            )
            df["Days In Use"] = (df["depleted_at"] - df["in_use_at"]).dt.days.where(
                df["depleted_at"].notna() & df["in_use_at"].notna()
            )
            df["Total Days"] = (df["depleted_at"] - df["in_stock_at"]).dt.days.where(
                df["depleted_at"].notna() & df["in_stock_at"].notna()
            )

            item_list = ["All Items"] + sorted(df["item_code"].dropna().unique().tolist())
            selected_item = st.selectbox("Select Item to View", item_list)

            if selected_item != "All Items":
                df = df[df["item_code"] == selected_item]

            st.dataframe(df, use_container_width=True)
        else:
            st.info("No items in inventory to display.")

    except Exception as e:
        st.error(f"Error fetching inventory overview: {e}")

    # -------- Allowed items management --------
    st.subheader("Allowed Items")
    allowed_items_data = supabase.from_("allowed_items").select("item_name").order("item_name").execute().data
    allowed_items_list = [item["item_name"] for item in allowed_items_data]

    with st.form("add_allowed_item", clear_on_submit=True):
        new_item = st.text_input("New item name", placeholder="e.g., MAYO_SAUCE")
        if st.form_submit_button("Add New Item"):
            if new_item.strip():
                try:
                    supabase.from_("allowed_items").insert({"item_name": new_item.strip()}).execute()
                    st.success(f"Added allowed item: **{new_item.strip()}**")
                    st.rerun()
                except Exception as e:
                    st.error(f"Item `{new_item.strip()}` already exists. Details: {e}")
            else:
                st.warning("Please enter an item name.")

    st.markdown("---")

    if allowed_items_list:
        items_to_delete = st.multiselect("Select items to delete:", allowed_items_list, key="delete_items")
        if st.button("Delete Selected Items", key="delete_selected_items"):
            if items_to_delete:
                try:
                    supabase.from_("allowed_items").delete().in_("item_name", items_to_delete).execute()
                    st.success(f"Deleted items: **{', '.join(items_to_delete)}**")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error deleting items: {e}")
            else:
                st.warning("Please select at least one item to delete.")

    # -------- User management --------
    st.subheader("User Management")
    users_data = supabase.from_("users").select("username, role").order("username").execute().data
    df_users = pd.DataFrame(users_data)
    st.dataframe(df_users)

    with st.form("add_user", clear_on_submit=True):
        nu = st.text_input("New username")
        npw = st.text_input("New password", type="password")
        nrole = st.selectbox("Role", ["truck", "admin"])
        if st.form_submit_button("Add User"):
            if nu.strip() and npw.strip():
                try:
                    supabase.from_("users").insert(
                        {"username": nu.strip(), "password": npw.strip(), "role": nrole}
                    ).execute()
                    st.success(f"User **{nu.strip()}** added.")
                    st.rerun()
                except Exception as e:
                    st.error(f"User `{nu.strip()}` already exists. Details: {e}")
            else:
                st.warning("Please fill in both username and password.")

    users_to_delete_data = (
        supabase.from_("users")
        .select("username")
        .neq("username", st.session_state.admin_username)
        .order("username")
        .execute()
        .data
    )
    users_to_delete = [user["username"] for user in users_to_delete_data]

    if users_to_delete:
        user_to_delete = st.selectbox("Select user to delete:", users_to_delete, key="user_select_delete")
        if st.button("Delete Selected User"):
            st.session_state.pending_delete_user = user_to_delete

    if st.session_state.pending_delete_user:
        ud = st.session_state.pending_delete_user
        st.warning(f"Are you sure you want to delete user: **{ud}**?")
        c1, c2 = st.columns(2)
        if c1.button("Yes, delete"):
            supabase.from_("users").delete().eq("username", ud).execute()
            st.success(f"Deleted user **{ud}**.")
            st.session_state.pending_delete_user = None
            st.rerun()
        if c2.button("Cancel"):
            st.session_state.pending_delete_user = None

    # --- Clear Inventory with Double Verification ---
    st.subheader("Clear Inventory")
    if not st.session_state.confirm_clear_inventory:
        if st.button("Clear Entire Inventory", type="primary"):
            st.session_state.confirm_clear_inventory = True
    else:
        st.warning("Are you sure you want to clear the entire inventory? This cannot be undone.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Yes, Clear"):
                inventory_data = supabase.from_("inventory").select("id").execute().data
                for item in inventory_data:
                    supabase.from_("inventory").delete().eq("id", item["id"]).execute()

                st.success("Inventory cleared successfully!")
                st.session_state.confirm_clear_inventory = False
                st.rerun()
        with col2:
            if st.button("Cancel"):
                st.session_state.confirm_clear_inventory = False


def management_mode():
    st.header("Truck Management")

    if not st.session_state.admin_logged_in:
        username = st.text_input("Admin Username")
        password = st.text_input("Admin Password", type="password")
        if st.button("Login as Admin"):
            is_valid, role = check_login(username, password)
            if is_valid and role == "admin":
                st.session_state.admin_logged_in = True
                st.session_state.admin_username = username
            else:
                st.error("Invalid credentials.")
        return

    st.write(f"Logged in as **{st.session_state.admin_username}**")
    if st.button("Logout"):
        st.session_state.admin_logged_in = False
        st.session_state.admin_username = ""
        return

    st.markdown("---")

    st.subheader("Create Anticipated Truck")
    with st.form("create_truck_form", clear_on_submit=True):
        truck_name = st.text_input("Truck Name")

        days = ["Monday", "Thursday", "Saturday"]
        selected_day = st.selectbox("Truck Day", days)

        allowed_data = supabase.from_("allowed_items").select("item_name").order("item_name").execute().data
        allowed_items = [item["item_name"] for item in allowed_data]

        qtys = {}
        for item in allowed_items:
            qtys[item] = st.number_input(f"{item} quantity", min_value=0, max_value=99, step=1, key=f"qty_{item}")

        skip_slots = st.number_input(
            "Number of label slots to skip (for partially used sticker sheets)",
            min_value=0,
            max_value=29,
            step=1,
            value=0,
        )

        submit_button = st.form_submit_button("Generate Anticipated Truck")

    if submit_button:
        if not truck_name.strip():
            st.error("Please enter a name for the truck.")
        else:
            try:
                now = datetime.utcnow().isoformat()

                truck_response = supabase.from_("anticipated_trucks").insert(
                    {
                        "truck_name": truck_name,
                        "created_by": st.session_state.admin_username,
                        "created_at": now,
                        "day_of_week": selected_day,
                        "status": "open",
                    }
                ).execute()

                truck_id = truck_response.data[0]["id"]

                # Reset per-truck batch cache so slots assign 1..99 cleanly for THIS generation
                st.session_state.batch_assigned_slots = {}
                global batch_assigned_slots
                batch_assigned_slots = st.session_state.batch_assigned_slots

                barcodes = []
                items_to_insert = []

                for item, qty in qtys.items():
                    for _ in range(int(qty)):
                        slot = get_next_slot(item)
                        label = f"{item}_{slot}"
                        items_to_insert.append(
                            {
                                "truck_id": truck_id,
                                "item_code": item,
                                "slot": slot,
                                "barcode_label": label,
                                "status": "pending",
                            }
                        )
                        png = generate_barcode_bytes(label)
                        barcodes.append((label, png))

                if items_to_insert:
                    supabase.from_("anticipated_items").insert(items_to_insert).execute()

                pdf_data = create_barcode_pdf(barcodes, skip_slots=skip_slots)

                st.download_button(
                    "Download 10x3 Sticker Sheet (PDF)",
                    data=pdf_data,
                    file_name=f"{truck_name}_barcodes.pdf",
                    mime="application/pdf",
                    key=f"download_{truck_name}_{datetime.utcnow().timestamp()}",
                )

                st.success(f"Anticipated truck '{truck_name}' created for {selected_day}.")

            except Exception as e:
                st.error(f"Error creating truck: {e}")

    st.subheader("Truck Summary Dashboard")
    trucks_data = (
        supabase.from_("anticipated_trucks")
        .select("id, truck_name, created_at, status")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    trucks = pd.DataFrame(trucks_data)

    if trucks.empty:
        st.info("No anticipated trucks found.")
        return

    t_choice = st.selectbox(
        "Select truck to view",
        trucks.apply(lambda r: f"{r['id']} - {r['truck_name']} ({r['created_at']})", axis=1),
    )
    t_id = int(t_choice.split(" - ")[0])

    df_items_data = supabase.from_("anticipated_items").select("*").eq("truck_id", t_id).execute().data
    df_items = pd.DataFrame(df_items_data)

    total_count = len(df_items)
    received_count = len(df_items[df_items.get("status") == "scanned"]) if "status" in df_items.columns else 0
    missing_count = len(df_items[df_items.get("status") == "missing"]) if "status" in df_items.columns else 0
    pending_count = len(df_items[df_items.get("status") == "pending"]) if "status" in df_items.columns else 0

    st.markdown(
        f"""
        **Summary for Truck ID {t_id}:**
        - Total Anticipated: **{total_count}**
        - Received: **{received_count}**
        - Missing: **{missing_count}**
        - Pending Scans: **{pending_count}**
        """
    )

    if not df_items.empty and "item_code" in df_items.columns and "status" in df_items.columns:
        breakdown = df_items.groupby(["item_code", "status"]).size().unstack(fill_value=0)
        st.dataframe(breakdown)
    else:
        st.info("No anticipated items found for this truck.")

    st.markdown("---")
    st.subheader("Actions for Selected Truck")
    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Reprint Barcode Pages"):
            if not df_items.empty:
                barcodes_to_reprint = []
                for _, row in df_items.iterrows():
                    png = generate_barcode_bytes(row["barcode_label"])
                    barcodes_to_reprint.append((row["barcode_label"], png))
                pdf_data = create_barcode_pdf(barcodes_to_reprint)
                truck_name = trucks[trucks["id"] == t_id]["truck_name"].iloc[0]
                st.download_button(
                    label=f"Download Barcodes for {truck_name}",
                    data=pdf_data,
                    file_name=f"{truck_name}_reprint.pdf",
                    mime="application/pdf",
                )
            else:
                st.warning("No barcodes to reprint for this truck.")

    truck_data = trucks[trucks["id"] == t_id].iloc[0]
    truck_name = truck_data["truck_name"]
    truck_status = str(truck_data.get("status", "open")).lower()

    with col2:
        if truck_status == "closed":
            st.info(f"Truck **{truck_name}** is already closed.")
        else:
            if pending_count > 0:
                if st.button(f"Close {truck_name} (Mark Pending as Missing)", key=f"close_truck_{t_id}"):
                    now = datetime.utcnow().isoformat()
                    total_items = len(df_items)
                    items_processed = len(df_items[df_items["status"] == "scanned"]) if "status" in df_items.columns else 0

                    supabase.from_("anticipated_items").update({"status": "missing"}).eq("truck_id", t_id).eq(
                        "status", "pending"
                    ).execute()

                    supabase.from_("anticipated_trucks").update({"status": "closed"}).eq("id", t_id).execute()

                    supabase.from_("analytics_history").insert(
                        {
                            "truck_id": t_id,
                            "closed_by": st.session_state.admin_username,
                            "closed_at": now,
                            "items_processed": items_processed,
                            "items_missing": pending_count,
                            "total_items": total_items,
                        }
                    ).execute()

                    st.success(f"Truck **{truck_name}** closed. Missing items marked.")
                    st.rerun()
            else:
                if st.button(f"Close {truck_name}", key=f"force_close_{t_id}"):
                    now = datetime.utcnow().isoformat()
                    total_items = len(df_items)
                    items_processed = len(df_items[df_items["status"] == "scanned"]) if "status" in df_items.columns else 0

                    supabase.from_("anticipated_trucks").update({"status": "closed"}).eq("id", t_id).execute()

                    supabase.from_("analytics_history").insert(
                        {
                            "truck_id": t_id,
                            "closed_by": st.session_state.admin_username,
                            "closed_at": now,
                            "items_processed": items_processed,
                            "items_missing": 0,
                            "total_items": total_items,
                        }
                    ).execute()

                    st.success(f"Truck **{truck_name}** closed. All items already processed.")
                    st.rerun()

    with col3:
        if st.session_state.confirm_delete_truck != t_id:
            if st.button(f"Delete {truck_name}", key=f"delete_{t_id}"):
                st.session_state.confirm_delete_truck = t_id
        else:
            st.warning(f"Are you sure you want to delete **{truck_name}** and ALL related data?")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Yes, Delete", key=f"yes_delete_{t_id}"):
                    supabase.from_("analytics_history").delete().eq("truck_id", t_id).execute()
                    supabase.from_("inventory").delete().eq("truck_id", t_id).execute()
                    supabase.from_("anticipated_items").delete().eq("truck_id", t_id).execute()
                    supabase.from_("anticipated_trucks").delete().eq("id", t_id).execute()
                    st.success(f"Truck **{truck_name}** and all related data were deleted.")
                    st.session_state.confirm_delete_truck = None
                    st.rerun()
            with c2:
                if st.button("Cancel", key=f"cancel_delete_{t_id}"):
                    st.session_state.confirm_delete_truck = None


def analytics_mode():
    st.header("Analytics Mode")

    if not st.session_state.get("admin_logged_in", False):
        username = st.text_input("Admin Username")
        password = st.text_input("Admin Password", type="password")

        if st.button("Login as Admin"):
            is_valid, role = check_login(username, password)
            if is_valid and role == "admin":
                st.session_state.admin_logged_in = True
                st.session_state.admin_username = username
            else:
                st.error("Invalid credentials.")
        return

    st.subheader("Truck History")
    try:
        trucks_data = (
            supabase.from_("anticipated_trucks")
            .select("id, truck_name, created_by, created_at")
            .order("created_at", desc=True)
            .execute()
            .data
        )
        trucks = pd.DataFrame(trucks_data)
    except Exception as e:
        st.error(f"Error fetching truck data: {e}")
        trucks = pd.DataFrame()

    if trucks.empty:
        st.info("No trucks found.")
        return

    truck_options = {f"{row['truck_name']} (ID {row['id']})": row["id"] for _, row in trucks.iterrows()}
    selected_truck_name = st.selectbox("Select a truck to view history:", list(truck_options.keys()))
    t_id = truck_options[selected_truck_name]

    try:
        truck_info_data = (
            supabase.from_("anticipated_trucks").select("created_by, created_at").eq("id", t_id).execute().data
        )
        truck_info = truck_info_data[0] if truck_info_data else None
        created_by, created_at = (
            (truck_info["created_by"], truck_info["created_at"]) if truck_info else ("Unknown", "Unknown")
        )

        scanned_by_data = (
            supabase.from_("inventory").select("added_by").eq("truck_id", t_id).eq("status", "in_stock").execute().data
        )
        scanned_users = {item["added_by"] for item in scanned_by_data} if scanned_by_data else set()
        scanned_by = ", ".join(sorted(scanned_users)) if scanned_users else "No scans yet"

        closed_info_data = (
            supabase.from_("analytics_history").select("closed_by, closed_at").eq("truck_id", t_id).execute().data
        )
        closed_info = closed_info_data[0] if closed_info_data else None
        closed_by, closed_at = (
            (closed_info["closed_by"], closed_info["closed_at"]) if closed_info else ("Not closed yet", "")
        )

    except Exception as e:
        st.error(f"Error fetching truck history details: {e}")
        created_by, created_at = "Error", "Error"
        scanned_by = "Error"
        closed_by, closed_at = "Error", ""

    st.markdown(
        f"""
        **Truck:** {selected_truck_name}  
        **Created by:** {created_by} at {created_at}  
        **Scanned by:** {scanned_by}  
        **Closed by:** {closed_by} {f'at {closed_at}' if closed_at else ''}
        """
    )

    st.markdown("---")
    st.subheader("Item Lifespan Analysis")

    try:
        depleted_items_data = (
            supabase.from_("inventory")
            .select("item_code, in_use_at, depleted_at")
            .not_.is_("in_use_at", "null")
            .not_.is_("depleted_at", "null")
            .execute()
            .data
        )
        depleted_items = pd.DataFrame(depleted_items_data)

        if not depleted_items.empty:
            depleted_items["in_use_at"] = pd.to_datetime(depleted_items["in_use_at"], errors="coerce", utc=True)
            depleted_items["depleted_at"] = pd.to_datetime(depleted_items["depleted_at"], errors="coerce", utc=True)
            depleted_items["duration_days"] = (depleted_items["depleted_at"] - depleted_items["in_use_at"]).dt.days

            avg_lifespan = depleted_items.groupby("item_code")["duration_days"].mean().reset_index()
            avg_lifespan.rename(columns={"duration_days": "Average Lifespan (Days)"}, inplace=True)
            st.dataframe(avg_lifespan)
        else:
            st.info("Not enough data to calculate item lifespans.")
    except Exception as e:
        st.error(f"Error fetching item lifespan data: {e}")

    st.markdown("---")
    st.subheader("Depletion Between Two Trucks")

    try:
        history_data = supabase.from_("analytics_history").select("truck_id, closed_at").order("closed_at").execute().data
        truck_history = pd.DataFrame(history_data)

        truck_names_data = supabase.from_("anticipated_trucks").select("id, truck_name").execute().data
        truck_names = pd.DataFrame(truck_names_data)

        if not truck_history.empty and not truck_names.empty:
            truck_history = truck_history.merge(truck_names, left_on="truck_id", right_on="id", how="left").drop(
                "id", axis=1
            )
            truck_history["label"] = truck_history.apply(
                lambda r: f"{r['truck_name']} (ID {r['truck_id']}) - Closed {r['closed_at']}", axis=1
            )
    except Exception as e:
        st.error(f"Error fetching data for depletion analysis: {e}")
        truck_history = pd.DataFrame()

    if len(truck_history) >= 2:
        col1, col2 = st.columns(2)
        with col1:
            truck1_label = st.selectbox("Select First Truck:", truck_history["label"], index=len(truck_history) - 2)
        with col2:
            truck2_label = st.selectbox("Select Second Truck:", truck_history["label"], index=len(truck_history) - 1)

        truck1 = truck_history[truck_history["label"] == truck1_label].iloc[0]
        truck2 = truck_history[truck_history["label"] == truck2_label].iloc[0]

        if truck1["truck_id"] == truck2["truck_id"]:
            st.warning("Please select two different trucks.")
        elif pd.to_datetime(truck1["closed_at"]) > pd.to_datetime(truck2["closed_at"]):
            st.error("The first truck's date must be before the second truck's date.")
        else:
            try:
                depletion_data = (
                    supabase.from_("inventory")
                    .select("item_code")
                    .gte("depleted_at", truck1["closed_at"])
                    .lte("depleted_at", truck2["closed_at"])
                    .execute()
                    .data
                )
                depletion_df = pd.DataFrame(depletion_data)

                if not depletion_df.empty:
                    depletion_counts = (
                        depletion_df.groupby("item_code").size().reset_index(name="depleted_count").sort_values(
                            "depleted_count", ascending=False
                        )
                    )
                    st.write(
                        f"Items depleted between **{truck1['truck_name']}** and **{truck2['truck_name']}**:"
                    )
                    st.dataframe(depletion_counts)
                else:
                    st.info("No items were depleted between the selected trucks.")
            except Exception as e:
                st.error(f"Error calculating depletion: {e}")
    elif len(truck_history) == 1:
        st.info("Please close a second truck in Truck Management to see depletion analysis.")
    else:
        st.info("No truck history available yet.")


# -----------------------------
# Main Mode Selector
# -----------------------------
mode = st.sidebar.selectbox(
    "Select Mode",
    ["User Mode", "Truck Mode", "Admin Mode", "Truck Management", "Analytics Mode"],
    index=0,
)

if mode == "User Mode":
    user_mode()
elif mode == "Truck Mode":
    truck_mode()
elif mode == "Admin Mode":
    admin_mode()
elif mode == "Truck Management":
    management_mode()
elif mode == "Analytics Mode":
    analytics_mode()


# jan 17., 2026
