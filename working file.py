# app.py
import streamlit as st
import sqlite3
import datetime
import pandas as pd
from barcode import Code128
from barcode.writer import ImageWriter
from io import BytesIO
import base64
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PIL import Image
import contextlib
import os
import tempfile
from supabase import create_client


# --- Supabase connection ---
SUPABASE_URL = "https://your-project-id.supabase.co"
SUPABASE_KEY = "your-anon-or-service-role-key"
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)



# --- Database setup ---
DB_NAME = "inventory.db"

def get_connection():
    """Establishes and returns a database connection."""
    return sqlite3.connect(DB_NAME)

def setup_database():
    """Creates the necessary tables if they don't exist and initializes the default admin user."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Table for allowed items
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS allowed_items (
                item_name TEXT PRIMARY KEY UNIQUE NOT NULL
            )
        """)
        
        # Table for users
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL
            )
        """)

        # Table for inventory
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_code TEXT NOT NULL,
                slot INTEGER NOT NULL,
                status TEXT NOT NULL,
                added_by TEXT NOT NULL,
                added_at TEXT NOT NULL,
                in_stock_at TEXT,
                in_use_at TEXT,
                depleted_at TEXT,
                UNIQUE (item_code, slot)
            )
        """)

        # Table for anticipated trucks
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anticipated_trucks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                truck_name TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT DEFAULT 'pending'
            )
        """)

        # Table for anticipated items
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anticipated_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                truck_id INTEGER NOT NULL,
                item_code TEXT NOT NULL,
                slot INTEGER NOT NULL,
                barcode_label TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                scanned_at TEXT,
                FOREIGN KEY (truck_id) REFERENCES anticipated_trucks (id)
            )
        """)
        
        # New table for analytics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analytics_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                truck_id INTEGER NOT NULL,
                items_processed INTEGER NOT NULL,
                closed_by TEXT NOT NULL,
                closed_at TEXT NOT NULL,
                FOREIGN KEY (truck_id) REFERENCES anticipated_trucks (id)
            )
        """)
        
        # Check if the users table is empty and create a default admin user if it is.
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", ("Lauren", "952426", "admin"))


            # --- Run migration to add missing column if not exists ---
        with get_connection() as conn:
            cursor = conn.cursor()
            # Try to add column safely
            try:
                cursor.execute("ALTER TABLE anticipated_trucks ADD COLUMN day_of_week TEXT")
                conn.commit()
            except Exception:
                # Ignore error if column already exists
                pass

        conn.commit()

setup_database()


st.set_page_config(page_title="Barcode Inventory App", layout="centered")
st.title("Barcode Inventory Management")

if "truck_logged_in" not in st.session_state:
    st.session_state.truck_logged_in = False
    st.session_state.truck_username = ""
    st.session_state.truck_role = ""
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
    st.session_state.admin_username = ""
if "pending_add" not in st.session_state:
    st.session_state.pending_add = None
if "last_barcode_b64" not in st.session_state:
    st.session_state.last_barcode_b64 = None
if "last_barcode_label" not in st.session_state:
    st.session_state.last_barcode_label = None
if "last_barcode_bytes" not in st.session_state:
    st.session_state.last_barcode_bytes = None
if "pending_delete_user" not in st.session_state:
    st.session_state.pending_delete_user = None

# ----------------- Helper functions -----------------
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from io import BytesIO
import tempfile, os, contextlib
from barcode import Code128
from barcode.writer import ImageWriter

def create_barcode_pdf(barcodes):
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=letter)
    page_w, page_h = letter

    # Layout: 3 columns × 10 rows
    margin_x = 36
    col_spacing = 20
    row_spacing = 15
    cols = 3
    rows = 10

    # Size of each slot
    sticker_w = (page_w - 2 * margin_x - (cols - 1) * col_spacing) / cols
    sticker_h = 60  # fixed height slot for each barcode

    # Compute total grid height for vertical centering
    grid_height = rows * sticker_h + (rows - 1) * row_spacing
    margin_y = (page_h - grid_height) / 2

    col, row = 0, 0
    temp_files = []
    try:
        for label, _ in barcodes:
            # generate barcode PNG without text
            barcode_obj = Code128(label, writer=ImageWriter())
            options = {
                "module_width": 0.35,   # slightly wider bars
                "module_height": 18,    # taller bars
                "write_text": False     # no text inside barcode
            }
            barcode_bytes = BytesIO()
            barcode_obj.write(barcode_bytes, options)

            # write to temp file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_img:
                temp_img.write(barcode_bytes.getvalue())
                temp_filepath = temp_img.name
                temp_files.append(temp_filepath)

            # position
            x_pos = margin_x + col * (sticker_w + col_spacing)
            y_pos = page_h - margin_y - (row + 1) * sticker_h - row * row_spacing

            # draw barcode image
            c.drawImage(
                temp_filepath,
                x_pos,
                y_pos + 12,  # shift up so label fits underneath
                width=sticker_w,
                height=sticker_h - 20,
                preserveAspectRatio=True,
                anchor='n'
            )

            # draw label under barcode
            c.setFont("Helvetica-Bold", 10)
            c.drawCentredString(
                x_pos + sticker_w / 2,
                y_pos,   # directly under the image
                label
            )

            # move to next slot
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


def handle_user_scan_auto():
    scanned_code = st.session_state.user_scan_input
    
    # Reset any previous scan data and success message
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

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM allowed_items WHERE item_name = ?", (item_code,))
            if not cursor.fetchone():
                st.error("NOT REGISTERED: This item code is not in the allowed list.")
                return

            cursor.execute("SELECT status FROM inventory WHERE item_code = ? AND slot = ?", (item_code, slot))
            row = cursor.fetchone()
            
            if not row:
                st.error("Item not found in inventory. Please check the barcode or add it first.")
                return

            current_status = row[0]
            st.session_state.user_mode_scan_data = {
                "item_code": item_code,
                "slot": slot,
                "current_status": current_status
            }
            st.success(f"Scanned: **{item_code}**, Slot **{slot}**. Current Status: **{current_status}**")

            # FIFO hint logic
            if current_status == 'in_stock':
                cursor.execute("""
                    SELECT slot FROM inventory
                    WHERE item_code = ? AND status = 'in_stock'
                    ORDER BY added_at ASC
                    LIMIT 1
                """, (item_code,))
                oldest = cursor.fetchone()
                if oldest:
                    oldest_slot = oldest[0]
                    if oldest_slot == slot:
                        st.markdown('<div style="background-color:#28a745;color:white;padding:10px;border-radius:5px;text-align:center;">FIFO HINT: USE THIS ITEM FIRST</div>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<div style="background-color:#dc3545;color:white;padding:10px;border-radius:5px;text-align:center;">FIFO HINT: **{item_code}_{oldest_slot}** is first</div>', unsafe_allow_html=True)


    except (ValueError, IndexError):
        st.error("Invalid format. Use `itemcode_slot` (e.g., `CFA_SAUCE_1`).")




def generate_barcode_bytes(label_text: str) -> bytes:
    buf = BytesIO()
    Code128(label_text, writer=ImageWriter()).write(buf, options={"write_text": True})
    buf.seek(0)
    return buf.getvalue()

def show_last_barcode():
    if st.session_state.last_barcode_b64:
        st.subheader("Last Generated Barcode")
        st.image(st.session_state.last_barcode_bytes, caption=st.session_state.last_barcode_label, width=300)
        st.download_button(
            label="Download & Print Barcode",
            data=st.session_state.last_barcode_bytes,
            file_name=f"{st.session_state.last_barcode_label}.png",
            mime="image/png"
        )
        st.info("The last generated barcode is saved here until a new one is created. Click 'Download' to save the image to your computer, then print it.")

def get_next_slot(item_code):
    with get_connection() as conn:
        cursor = conn.cursor()

        # Get slots already occupied for this item
        cursor.execute("""
            SELECT slot 
            FROM inventory 
            WHERE item_code = ? AND status IN ('in_stock', 'in_use')
        """, (item_code,))
        inventory_slots = {row[0] for row in cursor.fetchall()}

        cursor.execute("""
            SELECT slot 
            FROM anticipated_items 
            WHERE item_code = ?
        """, (item_code,))
        anticipated_slots = {row[0] for row in cursor.fetchall()}

        used_slots = inventory_slots.union(anticipated_slots)

    print(f"Item: {item_code}, Used slots: {used_slots}")

    # Loop 1–65, skip any taken
    for i in range(1, 66):
        if i not in used_slots:
            return i

    # If all 65 taken, wrap back to 1
    return 1





def check_login(username, password_input):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password, role FROM users WHERE username = ?", (username,))
        user_data = cursor.fetchone()
        if user_data:
            stored_password = user_data[0]
            role = user_data[1]
            if password_input == stored_password:
                return True, role
    return False, None

def close_truck(truck_id, closed_by):
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        c = conn.cursor()

        # Mark anticipated truck as closed
        c.execute("""
            UPDATE anticipated_trucks
            SET status='closed'
            WHERE id=?
        """, (truck_id,))

        # Record in analytics_history
        c.execute("""
            INSERT INTO analytics_history (truck_id, closed_by, closed_at)
            VALUES (?, ?, ?)
        """, (truck_id, closed_by, now))

        conn.commit()
    st.success(f"Truck {truck_id} closed by {closed_by} at {now}.")

# ----------------- Mode functions -----------------
def truck_mode():
    st.header("Truck Mode")

    # --- 1. Login/Logout Section ---
    if not st.session_state.get('truck_logged_in', False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.button("Login"):
            is_valid, role = check_login(username, password)
            if is_valid and role in ['truck', 'admin']:
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
    st.markdown("---")

    # --- 2. Truck Selection Section ---
    st.subheader("Select a Truck to Process")
    
    with get_connection() as conn:
        trucks = pd.read_sql(
            "SELECT id, truck_name, created_at, status FROM anticipated_trucks ORDER BY created_at DESC", 
            conn
        )
    
    if not trucks.empty:
        truck_labels = trucks.apply(lambda r: f"ID {r['id']} - {r['truck_name']} ({r['created_at'].split('T')[0]})", axis=1)
        t_choice = st.selectbox("Select a truck from the list:", truck_labels)
        
        t_id = int(t_choice.split(" - ")[0].replace("ID ", ""))
        st.session_state.current_truck_id = t_id
        
        selected_truck = trucks[trucks['id'] == t_id].iloc[0]
        st.success(f"Selected Truck: **{selected_truck['truck_name']} (ID {t_id})**")
        st.markdown("---")
    else:
        st.info("No trucks available. Please contact an admin to add one.")
        return

    # --- NEW: Block scanning if truck is closed ---
    if selected_truck["status"] == "closed":
        st.warning("This truck has been CLOSED. Scanning and adding items is disabled.")
        return   # stop execution here, so scan/emergency add sections don’t show

    # --- 3. Scan Anticipated Barcode Section ---
    st.subheader("Scan Barcode")

    with st.form("scan_form", clear_on_submit=True):
        scan = st.text_input("Scan or enter barcode:", key="scanner_input")
        submit_button = st.form_submit_button("Confirm Scan")

        if submit_button and scan:
            with get_connection() as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT id, item_code, slot FROM anticipated_items
                    WHERE barcode_label=? AND status='pending' AND truck_id=?
                """, (scan, st.session_state.current_truck_id))
                row = c.fetchone()

                if row:
                    aid, code, slot = row
                    now = datetime.datetime.now().isoformat()
                    try:
                        # Mark anticipated item as scanned
                        c.execute("UPDATE anticipated_items SET status='scanned', scanned_at=? WHERE id=?", (now, aid))
                        
                        # Insert into inventory with truck_id
                        c.execute("""
                            INSERT INTO inventory (item_code, slot, status, added_by, added_at, in_stock_at, truck_id)
                            VALUES (?, ?, 'in_stock', ?, ?, ?, ?)
                        """, (code, slot, st.session_state.truck_username, now, now, st.session_state.current_truck_id))
                        
                        conn.commit()
                        st.success(f"Barcode `{scan}` successfully received for truck {st.session_state.current_truck_id}.")
                    except sqlite3.IntegrityError:
                        st.error("Error: This item is already in inventory.")
                else:
                    st.error(f"Barcode `{scan}` not found, not pending, or does not belong to truck {st.session_state.current_truck_id}.")


    st.markdown("---")
    
    # --- 4. Reprint & Emergency Add Sections (Unchanged, but will be skipped if closed) ---
    st.subheader("Reprint Existing Barcode")
    with get_connection() as conn:
        df = pd.read_sql("SELECT item_code, slot FROM inventory WHERE status='in_stock'", conn)
    
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
    with get_connection() as conn:
        allowed = [r[0] for r in conn.execute("SELECT item_name FROM allowed_items ORDER BY item_name").fetchall()]
    
    if allowed:
        with st.form("emergency_add_form"):
            e_item = st.selectbox("Select item:", allowed)
            if st.form_submit_button("Add Emergency Item"):
                slot = get_next_slot(e_item)
                label = f"{e_item}_{slot}"
                now = datetime.datetime.now().isoformat()
                
                with get_connection() as conn:
                    c = conn.cursor()
                    try:
                        c.execute("""
                            INSERT INTO inventory (item_code, slot, status, added_by, added_at, in_stock_at)
                            VALUES (?, ?, 'in_stock', ?, ?, ?)
                        """, (e_item, slot, st.session_state.truck_username, now, now))
                        conn.commit()
                        st.success(f"Emergency added `{label}` to inventory.")

                        png = generate_barcode_bytes(label)
                        st.session_state.last_barcode_bytes = png
                        st.session_state.last_barcode_label = label
                        st.session_state.last_barcode_b64 = base64.b64encode(png).decode('utf-8')
                    except sqlite3.IntegrityError:
                        st.error("Error adding item. This item-slot combination might already exist.")
        
        if st.session_state.get('last_barcode_b64'):
            show_last_barcode()
    else:
        st.warning("No allowed items are configured. Please contact an admin.")


def clear_user_scan():
    """Callback function to clear the user_scan session state."""
    st.session_state.user_scan = ""

# ----------------- User Mode Helpers -----------------
def reset_user_scan_state():
    st.session_state.user_mode_scan_data = None
    st.session_state.manual_update_visible = False

def process_scan_and_update(new_status, item_code, slot):
    now = datetime.datetime.now().isoformat()
    with get_connection() as conn:
        c = conn.cursor()
        # Update timestamps appropriately
        if new_status == 'in_use':
            c.execute("""
                UPDATE inventory
                SET status=?, in_use_at=?
                WHERE item_code=? AND slot=?
            """, (new_status, now, item_code, slot))
        elif new_status == 'depleted':
            c.execute("""
                UPDATE inventory
                SET status=?, depleted_at=?
                WHERE item_code=? AND slot=?
            """, (new_status, now, item_code, slot))
        elif new_status == 'in_stock':
            c.execute("""
                UPDATE inventory
                SET status=?, in_stock_at=?
                WHERE item_code=? AND slot=?
            """, (new_status, now, item_code, slot))
        conn.commit()
    st.session_state.update_success = f"Item `{item_code}_{slot}` updated to **{new_status}**."
    reset_user_scan_state()



def show_manual_options():
    st.session_state.manual_update_visible = True

# ----------------- User Mode -----------------
def user_mode():
    st.header("User Mode - Update Item Status")

    # --- Initialize session state ---
    if "update_success" not in st.session_state:
        st.session_state.update_success = None
    if "user_mode_scan_data" not in st.session_state:
        st.session_state.user_mode_scan_data = None
    if "manual_update_visible" not in st.session_state:
        st.session_state.manual_update_visible = False
    if "manual_update_done" not in st.session_state:
        st.session_state.manual_update_done = False
    if "manual_status_radio" not in st.session_state:
        st.session_state.manual_status_radio = "in_stock"  # default

    # --- Show last update success message ---
    if st.session_state.update_success:
        st.success(st.session_state.update_success)
        st.session_state.update_success = None

    # --- Scan input ---
    st.text_input(
        "Scan or enter barcode (format: itemcode_slot)",
        key="user_scan_input",
        on_change=handle_user_scan_auto
    )

    scan_data = st.session_state.user_mode_scan_data
    if not scan_data:
        return

    item_code = scan_data['item_code']
    slot = scan_data['slot']
    current_status = scan_data['current_status']

    st.info(f"Current status of **{item_code}_{slot}**: **{current_status}**")

    # --- Status update buttons ---
    if current_status == 'in_stock':
        st.button(
            "Mark as In Use",
            key=f"mark_in_use_{item_code}_{slot}",
            on_click=process_scan_and_update,
            args=('in_use', item_code, slot)
        )
    elif current_status == 'in_use':
        st.warning(f"Next step: mark **{item_code}_{slot}** as depleted")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.button(
                "Confirm Depletion",
                key=f"confirm_depletion_{item_code}_{slot}",
                on_click=process_scan_and_update,
                args=('depleted', item_code, slot)
            )
        with col2:
            st.button(
                "Cancel",
                key=f"cancel_in_use_{item_code}_{slot}",
                on_click=reset_user_scan_state
            )
        with col3:
            st.button(
                "Other Options",
                key=f"manual_override_{item_code}_{slot}",
                on_click=lambda: st.session_state.update({'manual_update_visible': True})
            )
    elif current_status == 'depleted':
        st.info("This item is already depleted.")
        col1, col2 = st.columns(2)
        with col1:
            st.button(
                "Mark as In Stock",
                key=f"mark_in_stock_{item_code}_{slot}",
                on_click=process_scan_and_update,
                args=('in_stock', item_code, slot)
            )
        with col2:
            st.button(
                "Cancel",
                key=f"cancel_depleted_{item_code}_{slot}",
                on_click=reset_user_scan_state
            )

    # --- Manual override ---
    if st.session_state.manual_update_visible:
        st.markdown("---")
        st.subheader("Manual Status Update")
        status_options = ["in_stock", "in_use", "depleted"]
        idx = status_options.index(current_status) if current_status in status_options else 0
        new_status_manual = st.radio(
            "Select new status:",
            status_options,
            index=idx,
            key=f"manual_status_radio_{item_code}_{slot}"
        )

        def confirm_manual_update():
            process_scan_and_update(new_status_manual, item_code, slot)
            st.session_state.manual_update_done = True
            st.session_state.manual_update_visible = False

        def cancel_manual_update():
            st.session_state.manual_update_visible = False
            st.session_state.manual_status_radio = current_status

        col1, col2 = st.columns(2)
        with col1:
            st.button(
                "Confirm Manual Update",
                key=f"confirm_manual_{item_code}_{slot}",
                on_click=confirm_manual_update
            )
        with col2:
            st.button(
                "Cancel",
                key=f"cancel_manual_{item_code}_{slot}",
                on_click=cancel_manual_update
            )

    # --- Show success message for manual override ---
    if st.session_state.manual_update_done:
        st.success(f"Status updated to **{st.session_state.manual_status_radio}**!")
        st.session_state.manual_update_done = False



def admin_mode():
    st.header("Admin Mode")

    if not st.session_state.admin_logged_in:
        username = st.text_input("Admin Username")
        password = st.text_input("Admin Password", type="password")
        if st.button("Login as Admin"):
            is_valid, role = check_login(username, password)
            if is_valid and role == 'admin':
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
    


    st.markdown("---")
    
    # -------- Product summary --------
    st.subheader("Product Summary")
    with get_connection() as conn:
        summary_df = pd.read_sql("""
            SELECT
                item_code,
                SUM(CASE WHEN status = 'in_stock' THEN 1 ELSE 0 END) AS "In Stock",
                SUM(CASE WHEN status = 'in_use' THEN 1 ELSE 0 END) AS "In Use",
                SUM(CASE WHEN status = 'depleted' AND depleted_at >= date('now', '-7 days') THEN 1 ELSE 0 END) AS "Depleted This Week"
            FROM inventory
            GROUP BY item_code
            ORDER BY item_code
        """, conn)
    st.dataframe(summary_df)

    # -------- Inventory summary + durations --------
    st.subheader("Inventory Overview")
    with get_connection() as conn:
        df = pd.read_sql("""
            SELECT item_code, slot, status, in_stock_at, in_use_at, depleted_at, added_at
            FROM inventory ORDER BY item_code, slot
        """, conn)
    
    def calculate_days(row, start_col, end_col):
        if pd.notna(row[start_col]) and pd.notna(row[end_col]):
            return (pd.to_datetime(row[end_col]) - pd.to_datetime(row[start_col])).days
        return None

    df["Days In Stock"] = df.apply(lambda row: calculate_days(row, "in_stock_at", "in_use_at"), axis=1)
    df["Days In Use"] = df.apply(lambda row: calculate_days(row, "in_use_at", "depleted_at"), axis=1)
    df["Total Days"] = df.apply(lambda row: calculate_days(row, "in_stock_at", "depleted_at"), axis=1)
    
    st.dataframe(df)

    # -------- Allowed items management --------
    st.subheader("Allowed Items")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT item_name FROM allowed_items ORDER BY item_name")
        allowed_items_list = [row[0] for row in cursor.fetchall()]

    with st.form("add_allowed_item", clear_on_submit=True):
        new_item = st.text_input("New item name", placeholder="e.g., MAYO_SAUCE")
        if st.form_submit_button("Add New Item"):
            if new_item.strip():
                with get_connection() as conn:
                    cursor = conn.cursor()
                    try:
                        cursor.execute(
                            "INSERT INTO allowed_items (item_name) VALUES (?)",
                            (new_item.strip(),)
                        )
                        conn.commit()
                        st.success(f"Added allowed item: **{new_item.strip()}**")
                    except sqlite3.IntegrityError:
                        st.error(f"Item `{new_item.strip()}` already exists.")
            else:
                st.warning("Please enter an item name.")


    # Deletion logic separate from the add form
    st.markdown("---")
    if allowed_items_list:
        items_to_delete = st.multiselect(
            "Select items to delete:", allowed_items_list, key="delete_items"
        )
        if st.button("Delete Selected Items", key="delete_selected_items"):
            if items_to_delete:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    placeholders = ','.join('?' for _ in items_to_delete)
                    cursor.execute(
                        f"DELETE FROM allowed_items WHERE item_name IN ({placeholders})",
                        items_to_delete
                    )
                    conn.commit()
                st.success(f"Deleted items: **{', '.join(items_to_delete)}**")
            else:
                st.warning("Please select at least one item to delete.")

    # -------- User management --------
    st.subheader("User Management")
    with get_connection() as conn:
        df_users = pd.read_sql("SELECT username, role FROM users ORDER BY username", conn)
    st.dataframe(df_users)
    
    with st.form("add_user", clear_on_submit=True):
        nu = st.text_input("New username")
        npw = st.text_input("New password", type="password")
        nrole = st.selectbox("Role", ["truck", "admin"])
        if st.form_submit_button("Add User"):
            if nu.strip() and npw.strip():
                with get_connection() as conn:
                    cursor = conn.cursor()
                    try:
                        cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                                        (nu.strip(), npw.strip(), nrole))
                        conn.commit()
                        st.success(f"User **{nu.strip()}** added.")
                    except sqlite3.IntegrityError:
                        st.error(f"User `{nu.strip()}` already exists.")
            else:
                st.warning("Please fill in both username and password.")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE username != ? ORDER BY username", (st.session_state.admin_username,))
        users_to_delete = [row[0] for row in cursor.fetchall()]

    if users_to_delete:
        user_to_delete = st.selectbox("Select user to delete:", users_to_delete, key="user_select_delete")
        if st.button("Delete Selected User"):
            st.session_state.pending_delete_user = user_to_delete
    
    if st.session_state.pending_delete_user:
        ud = st.session_state.pending_delete_user
        st.warning(f"Are you sure you want to delete user: **{ud}**?")
        c1, c2 = st.columns(2)
        if c1.button("Yes, delete"):
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users WHERE username = ?", (ud,))
                conn.commit()
            st.success(f"Deleted user **{ud}**.")
            st.session_state.pending_delete_user = None
        if c2.button("Cancel"):
            st.session_state.pending_delete_user = None

    # --- Clear Inventory with Double Verification ---
    st.subheader("Clear Inventory")

    if "confirm_clear_inventory" not in st.session_state:
        st.session_state.confirm_clear_inventory = False

    if not st.session_state.confirm_clear_inventory:
        if st.button("Clear Entire Inventory", type="primary"):
            st.session_state.confirm_clear_inventory = True
    else:
        st.warning("Are you sure you want to clear the entire inventory? This cannot be undone.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Yes, Clear"):
                with get_connection() as conn:
                    conn.execute("DELETE FROM inventory")
                    conn.commit()
                st.success("Inventory cleared successfully!")
                st.session_state.confirm_clear_inventory = False
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
            if is_valid and role == 'admin':
                st.session_state.admin_logged_in = True
                st.session_state.admin_username = username
            else:
                st.error("Invalid credentials.")
        return

    st.write(f"Logged in as **{st.session_state.admin_username}**")
    if st.button("Logout"):
        st.session_state.admin_logged_in = False
        st.session_state.admin_username = ""

    st.markdown("---")

    # ---------- Create Anticipated Truck ----------
    st.subheader("Create Anticipated Truck")
    with st.form("create_truck_form", clear_on_submit=True):
        truck_name = st.text_input("Truck Name")

        # Day-of-week dropdown
        days = ["Monday", "Thursday", "Saturday"]
        selected_day = st.selectbox("Truck Day", days)

        with get_connection() as conn:
            allowed = [r[0] for r in conn.execute("SELECT item_name FROM allowed_items ORDER BY item_name").fetchall()]

        qtys = {}
        for item in allowed:
            qtys[item] = st.number_input(f"{item} quantity", min_value=0, max_value=65, step=1, key=f"qty_{item}")

        submit_button = st.form_submit_button("Generate Anticipated Truck")

    if submit_button:
        if not truck_name.strip():
            st.error("Please enter a name for the truck.")
        else:
            with get_connection() as conn:
                c = conn.cursor()
                now = datetime.datetime.now().isoformat()

                # Insert truck WITH day_of_week
                c.execute("""
                    INSERT INTO anticipated_trucks (truck_name, created_by, created_at, day_of_week)
                    VALUES (?, ?, ?, ?)
                """, (truck_name, st.session_state.admin_username, now, selected_day))
                truck_id = c.lastrowid

                barcodes = []
                for item, qty in qtys.items():
                    for _ in range(qty):
                        slot = get_next_slot(item)
                        label = f"{item}_{slot}"

                        c.execute("""
                            INSERT INTO anticipated_items (truck_id, item_code, slot, barcode_label)
                            VALUES (?, ?, ?, ?)
                        """, (truck_id, item, slot, label))
                        conn.commit()  

                        png = generate_barcode_bytes(label)
                        barcodes.append((label, png))

                pdf_data = create_barcode_pdf(barcodes)
                st.download_button(
                    "Download 10x3 Sticker Sheet (PDF)",
                    data=pdf_data,
                    file_name=f"{truck_name}_barcodes.pdf",
                    mime="application/pdf",
                    key=f"download_{truck_name}_{datetime.datetime.now().timestamp()}" 
                )
                st.success(f"Anticipated truck '{truck_name}' created for {selected_day}.")





    # ---------- Truck Summary Dashboard ----------
    st.subheader("Truck Summary Dashboard")
    with get_connection() as conn:
        trucks = pd.read_sql("SELECT id, truck_name, created_at FROM anticipated_trucks ORDER BY created_at DESC", conn)
    
    if not trucks.empty:
        t_choice = st.selectbox("Select truck to view", trucks.apply(lambda r: f"{r['id']} - {r['truck_name']} ({r['created_at']})", axis=1))
        t_id = int(t_choice.split(" - ")[0])

        with get_connection() as conn:
            df_items = pd.read_sql("SELECT * FROM anticipated_items WHERE truck_id=?", conn, params=(t_id,))

        total_count = len(df_items)
        received_count = len(df_items[df_items["status"] == "scanned"])
        missing_count = len(df_items[df_items["status"] == "missing"])
        pending_count = len(df_items[df_items["status"] == "pending"])

        st.markdown(f"""
        **Summary for Truck ID {t_id}:**
        - Total Anticipated: **{total_count}**
        - Received: **{received_count}**
        - Missing: **{missing_count}**
        - Pending Scans: **{pending_count}**
        """)

        breakdown = df_items.groupby(["item_code", "status"]).size().unstack(fill_value=0)
        st.dataframe(breakdown)

        # Actions for selected truck
        st.markdown("---")
        st.subheader("Actions for Selected Truck")

        col1, col2, col3 = st.columns(3)

        # Reprint Barcodes button
        with col1:
            if st.button("Reprint Barcode Pages"):
                if not df_items.empty:
                    barcodes_to_reprint = []
                    for _, row in df_items.iterrows():
                        png = generate_barcode_bytes(row['barcode_label'])
                        barcodes_to_reprint.append((row['barcode_label'], png))
                    
                    pdf_data = create_barcode_pdf(barcodes_to_reprint)
                    st.download_button(
                        label=f"Download Barcodes for {trucks[trucks['id']==t_id]['truck_name'].iloc[0]}",
                        data=pdf_data,
                        file_name=f"{trucks[trucks['id']==t_id]['truck_name'].iloc[0]}_reprint.pdf",
                        mime="application/pdf"
                    )
                else:
                    st.warning("No barcodes to reprint for this truck.")

        # --- Close Truck Button ---
        truck_name = trucks[trucks['id'] == t_id]['truck_name'].iloc[0]  # same way as delete

        with col2:
            if pending_count > 0:
                if st.button(f"Close {truck_name} (Mark Pending as Missing)", key=f"close_truck_{t_id}"):
                    with get_connection() as conn:
                        c = conn.cursor()
                        now = datetime.datetime.now().isoformat()

                        # Get counts
                        c.execute("SELECT COUNT(*) FROM anticipated_items WHERE truck_id=?", (t_id,))
                        total_items = c.fetchone()[0] or 0

                        c.execute("SELECT COUNT(*) FROM anticipated_items WHERE truck_id=? AND status='missing'", (t_id,))
                        items_missing = c.fetchone()[0] or 0

                        c.execute("SELECT COUNT(*) FROM anticipated_items WHERE truck_id=? AND status='scanned'", (t_id,))
                        items_processed = c.fetchone()[0] or 0

                        # Mark pending items as missing
                        c.execute("""
                            UPDATE anticipated_items 
                            SET status='missing' 
                            WHERE truck_id=? AND status='pending'
                        """, (t_id,))

                        # Mark truck as closed
                        c.execute("""
                            UPDATE anticipated_trucks 
                            SET status='closed' 
                            WHERE id=?
                        """, (t_id,))

                        # Insert into analytics_history
                        c.execute("""
                            INSERT INTO analytics_history 
                            (truck_id, closed_by, closed_at, items_processed, items_missing, total_items)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (t_id, st.session_state.admin_username, now, items_processed, items_missing, total_items))

                        conn.commit()
                    st.success(f"Truck **{truck_name}** closed. Missing items marked.")
            else:
                if st.button(f"Close {truck_name}", key=f"force_close_{t_id}"):
                    with get_connection() as conn:
                        c = conn.cursor()
                        now = datetime.datetime.now().isoformat()

                        # Get counts
                        c.execute("SELECT COUNT(*) FROM anticipated_items WHERE truck_id=?", (t_id,))
                        total_items = c.fetchone()[0] or 0

                        c.execute("SELECT COUNT(*) FROM anticipated_items WHERE truck_id=? AND status='missing'", (t_id,))
                        items_missing = c.fetchone()[0] or 0

                        c.execute("SELECT COUNT(*) FROM anticipated_items WHERE truck_id=? AND status='scanned'", (t_id,))
                        items_processed = c.fetchone()[0] or 0

                        # Mark truck as closed
                        c.execute("""
                            UPDATE anticipated_trucks 
                            SET status='closed' 
                            WHERE id=?
                        """, (t_id,))

                        # Insert into analytics_history
                        c.execute("""
                            INSERT INTO analytics_history 
                            (truck_id, closed_by, closed_at, items_processed, items_missing, total_items)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (t_id, st.session_state.admin_username, now, items_processed, items_missing, total_items))

                        conn.commit()
                    st.success(f"Truck **{truck_name}** closed. All items already processed.")




        
            # --- Delete Truck with Double Verification ---
            if "confirm_delete_truck" not in st.session_state:
                st.session_state.confirm_delete_truck = None  # stores truck ID pending deletion

            if st.session_state.confirm_delete_truck != t_id:
                if st.button(f"Delete {trucks[trucks['id']==t_id]['truck_name'].iloc[0]}", key=f"delete_{t_id}"):
                    st.session_state.confirm_delete_truck = t_id
            else:
                truck_name = trucks[trucks['id']==t_id]['truck_name'].iloc[0]
                st.warning(f"Are you sure you want to delete **{truck_name}** and all related anticipated items?")
                col1, col2 = st.columns(2)

                with col1:
                    if st.button("Yes, Delete", key=f"yes_delete_{t_id}"):
                        with get_connection() as conn:
                            conn.execute("DELETE FROM anticipated_items WHERE truck_id=?", (t_id,))
                            conn.execute("DELETE FROM anticipated_trucks WHERE id=?", (t_id,))
                            conn.commit()
                        st.success(f"Truck **{truck_name}** and its items were deleted.")
                        st.session_state.confirm_delete_truck = None

                with col2:
                    if st.button("Cancel", key=f"cancel_delete_{t_id}"):
                        st.session_state.confirm_delete_truck = None



    else:
        st.info("No anticipated trucks found.")


def analytics_mode():
    st.header("Analytics Mode")

    # --- Admin login check ---
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
        return  # Stop rendering if not logged in

    # --- Truck History ---
    st.subheader("Truck History")

    with get_connection() as conn:
        trucks = pd.read_sql("SELECT * FROM anticipated_trucks ORDER BY created_at DESC", conn)

    if not trucks.empty:
        truck_options = {f"{row['truck_name']} (ID {row['id']})": row['id'] for _, row in trucks.iterrows()}
        selected_truck_name = st.selectbox("Select a truck to view history:", list(truck_options.keys()))
        t_id = truck_options[selected_truck_name]

        with get_connection() as conn:
            truck_info = conn.execute("""
                SELECT created_by, created_at FROM anticipated_trucks WHERE id=?
            """, (t_id,)).fetchone()
            created_by, created_at = truck_info if truck_info else ("Unknown", "Unknown")

            scanned_by_rows = conn.execute("""
                SELECT DISTINCT added_by FROM inventory 
                WHERE truck_id=? AND status='in_stock'
            """, (t_id,)).fetchall()
            scanned_by = ", ".join([r[0] for r in scanned_by_rows]) if scanned_by_rows else "No scans yet"

            closed_info = conn.execute("""
                SELECT closed_by, closed_at FROM analytics_history
                WHERE truck_id=?
            """, (t_id,)).fetchone()
            closed_by, closed_at = closed_info if closed_info else ("Not closed yet", "")

        st.markdown(f"""
        **Truck:** {selected_truck_name}  
        **Created by:** {created_by} at {created_at}  
        **Scanned by:** {scanned_by}  
        **Closed by:** {closed_by} {f'at {closed_at}' if closed_at else ''}
        """)
    else:
        st.info("No trucks found.")

    # --- Item Lifespan Analysis ---
    st.markdown("---")
    st.subheader("Item Lifespan Analysis")

    with get_connection() as conn:
        depleted_items = pd.read_sql("""
            SELECT item_code, in_use_at, depleted_at
            FROM inventory
            WHERE in_use_at IS NOT NULL AND depleted_at IS NOT NULL
        """, conn)

    if not depleted_items.empty:
        depleted_items['in_use_at'] = pd.to_datetime(depleted_items['in_use_at'])
        depleted_items['depleted_at'] = pd.to_datetime(depleted_items['depleted_at'])
        depleted_items['duration_days'] = (depleted_items['depleted_at'] - depleted_items['in_use_at']).dt.days
        
        avg_lifespan = depleted_items.groupby('item_code')['duration_days'].mean().reset_index()
        avg_lifespan.rename(columns={'duration_days': 'Average Lifespan (Days)'}, inplace=True)
        st.dataframe(avg_lifespan)
    else:
        st.info("Not enough data to calculate item lifespans.")

    # --- Depletion Between Two Trucks ---
    st.markdown("---")
    st.subheader("Depletion Between Two Trucks")

    with get_connection() as conn:
        truck_history = pd.read_sql("""
            SELECT ah.truck_id, ah.closed_at, t.truck_name
            FROM analytics_history ah
            JOIN anticipated_trucks t ON ah.truck_id = t.id
            ORDER BY ah.closed_at ASC
        """, conn)

    if len(truck_history) >= 2:
        truck_history['label'] = truck_history.apply(
            lambda r: f"{r['truck_name']} (ID {r['truck_id']}) - Closed {r['closed_at']}", axis=1
        )

        col1, col2 = st.columns(2)
        with col1:
            truck1_label = st.selectbox("Select First Truck:", truck_history['label'], index=len(truck_history)-2)
        with col2:
            truck2_label = st.selectbox("Select Second Truck:", truck_history['label'], index=len(truck_history)-1)

        truck1 = truck_history[truck_history['label'] == truck1_label].iloc[0]
        truck2 = truck_history[truck_history['label'] == truck2_label].iloc[0]

        if truck1['truck_id'] == truck2['truck_id']:
            st.warning("Please select two different trucks.")
        elif truck1['closed_at'] > truck2['closed_at']:
            st.error("The first truck's date must be before the second truck's date.")
        else:
            with get_connection() as conn:
                depletion_df = pd.read_sql("""
                    SELECT item_code, COUNT(*) AS depleted_count
                    FROM inventory
                    WHERE depleted_at BETWEEN ? AND ?
                    GROUP BY item_code
                    ORDER BY depleted_count DESC
                """, conn, params=(truck1['closed_at'], truck2['closed_at']))

            st.write(f"Items depleted between **{truck1['truck_name']}** and **{truck2['truck_name']}**:")
            if not depletion_df.empty:
                st.dataframe(depletion_df)
            else:
                st.info("No items were depleted between the selected trucks.")
    elif len(truck_history) == 1:
        st.info("Please close a second truck in Truck Management to see depletion analysis.")
    else:
        st.info("No truck history available yet.")




# ----------------- Main Mode Selector -----------------
setup_database()

# Initialize session state
if 'admin_logged_in' not in st.session_state:
    st.session_state.admin_logged_in = False
if 'truck_logged_in' not in st.session_state:
    st.session_state.truck_logged_in = False
if "last_processed_scan" not in st.session_state:
    st.session_state.last_processed_scan = ""


mode = st.sidebar.selectbox("Select Mode", ["User Mode", "Truck Mode", "Admin Mode", "Truck Management", "Analytics Mode"], index=0)

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


