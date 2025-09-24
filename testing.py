# app.py
import streamlit as st
import datetime
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

# Load environment variables
load_dotenv(".env")

# --- Supabase connection ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# functioning app
st.set_page_config(page_title="Barcode Inventory App", layout="centered")
st.title("Barcode Inventory Management")

# Initialize session state variables
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
if "user_scan_input" not in st.session_state:
    st.session_state.user_scan_input = ""
if "user_mode_scan_data" not in st.session_state:
    st.session_state.user_mode_scan_data = None
if "manual_update_visible" not in st.session_state:
    st.session_state.manual_update_visible = False
if "update_success" not in st.session_state:
    st.session_state.update_success = None


# ----------------- Helper functions -----------------

from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from barcode import Code128
from barcode.writer import ImageWriter
import tempfile
import os
import contextlib

def create_barcode_pdf(barcodes, skip_slots=0):
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=letter)
    page_w, page_h = letter

    # Layout: 3 columns × 10 rows
    margin_x = 36
    col_spacing = 20
    row_spacing = 15
    cols = 3
    rows = 10

    # Size of each sticker
    sticker_w = (page_w - 2 * margin_x - (cols - 1) * col_spacing) / cols
    sticker_h = 60  # fixed height for each barcode

    # Compute total grid height for vertical centering
    grid_height = rows * sticker_h + (rows - 1) * row_spacing
    margin_y = (page_h - grid_height) / 2

    # START POSITION BASED ON SKIP
    col = skip_slots % cols
    row = skip_slots // cols
    temp_files = []

    try:
        for label, _ in barcodes:
            # Generate barcode image in memory
            barcode_obj = Code128(label, writer=ImageWriter())
            options = {
                "module_width": 0.35,
                "module_height": 18,
                "write_text": False
            }
            barcode_bytes = BytesIO()
            barcode_obj.write(barcode_bytes, options)

            # Save to a temporary file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_img:
                temp_img.write(barcode_bytes.getvalue())
                temp_filepath = temp_img.name
                temp_files.append(temp_filepath)

            # Calculate position
            x_pos = margin_x + col * (sticker_w + col_spacing)
            y_pos = page_h - margin_y - (row + 1) * sticker_h - row * row_spacing

            # Draw the barcode image
            c.drawImage(
                temp_filepath,
                x_pos,
                y_pos + 12,  # shift image up to make space for text
                width=sticker_w,
                height=sticker_h - 20,
                preserveAspectRatio=True,
                anchor='n'
            )

            # Draw the label under the image
            c.setFont("Helvetica-Bold", 10)
            c.drawCentredString(
                x_pos + sticker_w / 2,
                y_pos,
                label
            )

            # Move to next position
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
        # Clean up temp files
        for f in temp_files:
            with contextlib.suppress(OSError):
                os.remove(f)


# Ensure default admin exists in Supabase
def ensure_default_admin():
    # Check if any users exist
    response = supabase.table("users").select("*").execute()
    if response.data is None or len(response.data) == 0:
        # Insert default admin
        supabase.table("users").insert({
            "username": "Lauren",
            "password": "952426",
            "role": "admin"
        }).execute()

# Call it at app startup
ensure_default_admin()


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

        # Supabase: Check if item is in allowed_items
        allowed_item = supabase.from_('allowed_items').select('item_name').eq('item_name', item_code).execute().data
        if not allowed_item:
            st.error("NOT REGISTERED: This item code is not in the allowed list.")
            return

        # Supabase: Check if item is in inventory
        inventory_item = supabase.from_('inventory').select('status').eq('item_code', item_code).eq('slot', slot).execute().data
        if not inventory_item:
            st.error("Item not found in inventory. Please check the barcode or add it first.")
            return

        current_status = inventory_item[0]['status']
        st.session_state.user_mode_scan_data = {
            "item_code": item_code,
            "slot": slot,
            "current_status": current_status
        }
        st.success(f"Scanned: **{item_code}**, Slot **{slot}**. Current Status: **{current_status}**")

        # Supabase: FIFO hint logic
        if current_status == 'in_stock':
            oldest_item = supabase.from_('inventory').select('slot').eq('item_code', item_code).eq('status', 'in_stock').order('added_at').limit(1).execute().data
            if oldest_item:
                oldest_slot = oldest_item[0]['slot']
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




def check_login(username, password_input):
    try:
        user_data = supabase.from_('users').select('password, role').eq('username', username).single().execute().data
        if user_data:
            stored_password = user_data['password']
            role = user_data['role']
            if password_input == stored_password:
                return True, role
    except Exception:
        # If the user is not found, the query will raise an exception.
        return False, None
    return False, None

def close_truck(truck_id, closed_by):
    now = datetime.datetime.now().isoformat()
    
    # Supabase: Mark anticipated truck as closed
    supabase.from_('anticipated_trucks').update({'status': 'closed'}).eq('id', truck_id).execute()
    
    # Supabase: Record in analytics_history
    supabase.from_('analytics_history').insert({
        'truck_id': truck_id,
        'closed_by': closed_by,
        'closed_at': now
    }).execute()
    
    st.success(f"Truck {truck_id} closed by {closed_by} at {now}.")


batch_assigned_slots = {}

def get_next_slot(item_code):
    if item_code not in batch_assigned_slots:
        batch_assigned_slots[item_code] = set()

    # Fetch slots from inventory (ignore depleted)
    inventory_items = supabase.from_('inventory') \
        .select('slot, status') \
        .eq('item_code', item_code) \
        .execute().data
    anticipated_items = supabase.from_('anticipated_items') \
        .select('slot') \
        .eq('item_code', item_code) \
        .execute().data

    # Get sets of slots
    used_slots = {
        int(item['slot']) for item in inventory_items
        if item.get('slot') and item.get('status') != 'depleted'
    } | {
        int(item['slot']) for item in anticipated_items if item.get('slot')
    } | batch_assigned_slots[item_code]

    depleted_slots = {
        int(item['slot']) for item in inventory_items
        if item.get('slot') and item.get('status') == 'depleted'
    }

    # Step 1: Find the highest used slot
    highest_used = max(used_slots) if used_slots else 0

    # Step 2: Assign next slot if it's within range
    if highest_used < 65:
        next_slot = highest_used + 1
        batch_assigned_slots[item_code].add(next_slot)
        return next_slot

    # Step 3: Wrap to lowest depleted slot if all 1-65 are used
    available_slots = sorted(depleted_slots - used_slots)
    if available_slots:
        slot = available_slots[0]
    else:
        slot = 1  # fallback if nothing else is open

    batch_assigned_slots[item_code].add(slot)
    return slot




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

    msg, sender = get_section_message("truck")
    if msg:
        st.info(f"📢 {msg}\n\n— *{sender}*")

    # --- 2. Truck Selection Section ---
    st.subheader("Select a Truck to Process")
    
    # Supabase: Fetching trucks
    trucks_data = supabase.from_('anticipated_trucks').select('id, truck_name, created_at, status').order('created_at', desc=True).execute().data
    trucks = pd.DataFrame(trucks_data)
    
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
        return
    
    # --- 3. Scan Anticipated Barcode Section ---
    st.subheader("Scan Barcode")
    with st.form("scan_form", clear_on_submit=True):
        scan = st.text_input("Scan or enter barcode:", key="scanner_input")
        submit_button = st.form_submit_button("Confirm Scan")
    
    if submit_button and scan:
        # Supabase: Check if barcode is pending for the selected truck
        scan_data = supabase.from_('anticipated_items').select('id, item_code, slot').eq('barcode_label', scan).eq('status', 'pending').eq('truck_id', st.session_state.current_truck_id).execute().data
        
        if scan_data:
            row = scan_data[0]
            aid, code, slot = row['id'], row['item_code'], row['slot']
            now = datetime.datetime.now().isoformat()
            
            try:
                # Supabase: Mark anticipated item as scanned
                supabase.from_('anticipated_items').update({'status': 'scanned', 'scanned_at': now}).eq('id', aid).execute()
                
                # Supabase: Insert into inventory
                supabase.from_('inventory').insert({
                    'item_code': code,
                    'slot': slot,
                    'status': 'in_stock',
                    'added_by': st.session_state.truck_username,
                    'added_at': now,
                    'in_stock_at': now,
                    'truck_id': st.session_state.current_truck_id
                }).execute()
                
                st.success(f"Barcode `{scan}` successfully received for truck {st.session_state.current_truck_id}.")
            except Exception as e:
                st.error(f"Error: An item with this barcode might already exist in inventory. Details: {e}")
        else:
            st.error(f"Barcode `{scan}` not found, not pending, or does not belong to truck {st.session_state.current_truck_id}.")
            
    st.markdown("---")

    # --- 4. Reprint & Emergency Add Sections ---
    st.subheader("Reprint Existing Barcode")
    # Supabase: Fetch items in stock
    in_stock_data = supabase.from_('inventory').select('item_code, slot').eq('status', 'in_stock').execute().data
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
    # Supabase: Fetch allowed items
    allowed_data = supabase.from_('allowed_items').select('item_name').order('item_name').execute().data
    allowed = [item['item_name'] for item in allowed_data]
    
    if allowed:
        with st.form("emergency_add_form"):
            e_item = st.selectbox("Select item:", allowed)
            if st.form_submit_button("Add Emergency Item"):
                slot = get_next_slot(e_item)
                label = f"{e_item}_{slot}"
                now = datetime.datetime.now().isoformat()
                
                try:
                    # Supabase: Insert into inventory
                    supabase.from_('inventory').insert({
                        'item_code': e_item,
                        'slot': slot,
                        'status': 'in_stock',
                        'added_by': st.session_state.truck_username,
                        'added_at': now,
                        'in_stock_at': now
                    }).execute()
                    
                    st.success(f"Emergency added `{label}` to inventory.")

                    png = generate_barcode_bytes(label)
                    st.session_state.last_barcode_bytes = png
                    st.session_state.last_barcode_label = label
                    st.session_state.last_barcode_b64 = base64.b64encode(png).decode('utf-8')
                    st.rerun()
                except Exception as e:
                    st.error(f"Error adding item. This item-slot combination might already exist. Details: {e}")
        
        if st.session_state.get('last_barcode_b64'):
            show_last_barcode()
    else:
        st.warning("No allowed items are configured. Please contact an admin.")

# ----------------- User Mode -----------------
def user_mode():
    st.header("User Mode - Update Item Status")

    msg, sender = get_section_message("truck")
    if msg:
        st.info(f"📢 {msg}\n\n— *{sender}*")

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
    if "user_scan_input" not in st.session_state:  
        st.session_state.user_scan_input = ""

    # --- Show last update success message ---
    if st.session_state.update_success:
        st.success(st.session_state.update_success)
        st.session_state.update_success = None

    # --- Scan input and clear button ---
    # Initialize clear flag if it doesn't exist
    if "clear_scan_box" not in st.session_state:
        st.session_state.clear_scan_box = False

    # Determine value to show in the text input
    scan_value = "" if st.session_state.clear_scan_box else st.session_state.get("user_scan_input", "")
    if st.session_state.clear_scan_box:
        st.session_state.clear_scan_box = False
        st.session_state.user_mode_scan_data = None  # Optional reset

    # Show the scan input
    st.text_input(
        "Scan or enter barcode (format: itemcode_slot)",
        key="user_scan_input",
        value=scan_value,
        on_change=handle_user_scan_auto
    )

    # "Clear Box" button BELOW the input
    if st.button("Clear Box"):
        st.session_state.clear_scan_box = True
        st.rerun()


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

# ----------------- User Mode Helpers -----------------
# The function below needs to be defined BEFORE it is called.
# It was in your previous prompt but it is important to include here too.
def process_scan_and_update(new_status, item_code, slot):
    now = datetime.datetime.now().isoformat()
    update_data = {'status': new_status}
    
    if new_status == 'in_use':
        update_data['in_use_at'] = now
    elif new_status == 'depleted':
        update_data['depleted_at'] = now
    elif new_status == 'in_stock':
        update_data['in_stock_at'] = now
    
    # Supabase: Update the item's status
    supabase.from_('inventory').update(update_data).eq('item_code', item_code).eq('slot', slot).execute()
    
    st.session_state.update_success = f"Item `{item_code}_{slot}` updated to **{new_status}**."
    reset_user_scan_state()

def reset_user_scan_state():
    st.session_state.user_mode_scan_data = None
    st.session_state.manual_update_visible = False

# ----------- Admin Mode ---------------
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

    msg, sender = get_section_message("truck")
    if msg:
        st.info(f"📢 {msg}\n\n— *{sender}*")

    # -------- Product summary --------
    st.subheader("Product Summary")
    # Supabase: Fetch data for product summary
    try:
        summary_data = supabase.from_('inventory').select('item_code, status, depleted_at').execute().data
        summary_df_raw = pd.DataFrame(summary_data)
        
        if not summary_df_raw.empty:
            # Convert to datetime and ensure timezone-aware
            summary_df_raw['depleted_at'] = pd.to_datetime(summary_df_raw['depleted_at'], errors='coerce')
            summary_df_raw['depleted_at'] = summary_df_raw['depleted_at'].dt.tz_localize('UTC', nonexistent='NaT', ambiguous='NaT')

            # Pivot table to get counts by status
            summary_df = summary_df_raw.groupby('item_code')['status'].value_counts().unstack(fill_value=0)

            # Calculate "Depleted This Week"
            one_week_ago = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=7)
            depleted_this_week = summary_df_raw[
                (summary_df_raw['status'] == 'depleted') &
                (summary_df_raw['depleted_at'] >= one_week_ago)
            ].groupby('item_code')['status'].count().rename('Depleted This Week')

            # Combine the DataFrames
            final_summary_df = (
                summary_df
                .reindex(columns=['in_stock', 'in_use', 'depleted'], fill_value=0)
                .rename(columns={'in_stock': 'In Stock', 'in_use': 'In Use', 'depleted': 'Depleted Total'})
                .join(depleted_this_week, how='left')
                .fillna(0)
            )

            st.dataframe(final_summary_df)
        else:
            st.info("No inventory data to display.")
    except Exception as e:
        st.error(f"Error fetching product summary: {e}")


    # -------- Inventory summary + durations --------
    st.subheader("Inventory Overview")
    # Supabase: Fetch all inventory data
    try:
        inventory_data = supabase.from_('inventory').select('item_code, slot, status, in_stock_at, in_use_at, depleted_at, added_at').order('item_code').order('slot').execute().data
        df = pd.DataFrame(inventory_data)

        if not df.empty:
            # Convert timestamp columns to datetime objects
            df["in_stock_at"] = pd.to_datetime(df["in_stock_at"])
            df["in_use_at"] = pd.to_datetime(df["in_use_at"])
            df["depleted_at"] = pd.to_datetime(df["depleted_at"])
            
            # Calculate durations in days
            df["Days In Stock"] = (df["in_use_at"] - df["in_stock_at"]).dt.days
            df["Days In Use"] = (df["depleted_at"] - df["in_use_at"]).dt.days
            df["Total Days"] = (df["depleted_at"] - df["in_stock_at"]).dt.days
            
            st.dataframe(df)
        else:
            st.info("No items in inventory to display.")
    except Exception as e:
        st.error(f"Error fetching inventory overview: {e}")

    # -------- Allowed items management --------
    st.subheader("Allowed Items")
    # Supabase: Fetch allowed items
    allowed_items_data = supabase.from_('allowed_items').select('item_name').order('item_name').execute().data
    allowed_items_list = [item['item_name'] for item in allowed_items_data]

    with st.form("add_allowed_item", clear_on_submit=True):
        new_item = st.text_input("New item name", placeholder="e.g., MAYO_SAUCE")
        if st.form_submit_button("Add New Item"):
            if new_item.strip():
                try:
                    # Supabase: Insert a new allowed item
                    supabase.from_('allowed_items').insert({'item_name': new_item.strip()}).execute()
                    st.success(f"Added allowed item: **{new_item.strip()}**")
                    st.rerun()
                except Exception as e:
                    st.error(f"Item `{new_item.strip()}` already exists. Details: {e}")
            else:
                st.warning("Please enter an item name.")
                
    st.markdown("---")
    
    # Deletion logic separate from the add form
    if allowed_items_list:
        items_to_delete = st.multiselect(
            "Select items to delete:", allowed_items_list, key="delete_items"
        )
        if st.button("Delete Selected Items", key="delete_selected_items"):
            if items_to_delete:
                try:
                    # Supabase: Delete selected items
                    supabase.from_('allowed_items').delete().in_('item_name', items_to_delete).execute()
                    st.success(f"Deleted items: **{', '.join(items_to_delete)}**")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error deleting items: {e}")
            else:
                st.warning("Please select at least one item to delete.")

    # -------- User management --------
    st.subheader("User Management")
    # Supabase: Fetch all users
    users_data = supabase.from_('users').select('username, role').order('username').execute().data
    df_users = pd.DataFrame(users_data)
    st.dataframe(df_users)
    
    with st.form("add_user", clear_on_submit=True):
        nu = st.text_input("New username")
        npw = st.text_input("New password", type="password")
        nrole = st.selectbox("Role", ["truck", "admin"])
        if st.form_submit_button("Add User"):
            if nu.strip() and npw.strip():
                try:
                    # Supabase: Insert a new user
                    supabase.from_('users').insert({'username': nu.strip(), 'password': npw.strip(), 'role': nrole}).execute()
                    st.success(f"User **{nu.strip()}** added.")
                    st.rerun()
                except Exception as e:
                    st.error(f"User `{nu.strip()}` already exists. Details: {e}")
            else:
                st.warning("Please fill in both username and password.")

    # Supabase: Fetch users to delete (excluding the current admin)
    users_to_delete_data = supabase.from_('users').select('username').neq('username', st.session_state.admin_username).order('username').execute().data
    users_to_delete = [user['username'] for user in users_to_delete_data]
    
    if users_to_delete:
        user_to_delete = st.selectbox("Select user to delete:", users_to_delete, key="user_select_delete")
        if st.button("Delete Selected User"):
            st.session_state.pending_delete_user = user_to_delete
    
    if st.session_state.pending_delete_user:
        ud = st.session_state.pending_delete_user
        st.warning(f"Are you sure you want to delete user: **{ud}**?")
        c1, c2 = st.columns(2)
        if c1.button("Yes, delete"):
            # Supabase: Delete the selected user
            supabase.from_('users').delete().eq('username', ud).execute()
            st.success(f"Deleted user **{ud}**.")
            st.session_state.pending_delete_user = None
            st.rerun()
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
                # Fetch all inventory IDs
                inventory_data = supabase.from_('inventory').select('id').execute().data

                # Delete each row
                for item in inventory_data:
                    supabase.from_('inventory').delete().eq('id', item['id']).execute()

                st.success("Inventory cleared successfully!")
                st.session_state.confirm_clear_inventory = False
                st.rerun()
        with col2:
            if st.button("Cancel"):
                st.session_state.confirm_clear_inventory = False

# ----------- Management Mode ---------

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

        # Supabase: Fetch allowed items
        allowed_data = supabase.from_('allowed_items').select('item_name').order('item_name').execute().data
        allowed_items = [item['item_name'] for item in allowed_data]

        # Quantity inputs
        qtys = {}
        for item in allowed_items:
            qtys[item] = st.number_input(f"{item} quantity", min_value=0, max_value=65, step=1, key=f"qty_{item}")

        # NEW: Number of label slots to skip
        skip_slots = st.number_input(
            "Number of label slots to skip (for partially used sticker sheets)", 
            min_value=0, max_value=29, step=1, value=0
        )

        submit_button = st.form_submit_button("Generate Anticipated Truck")

    # Handle form submission
    if submit_button:
        if not truck_name.strip():
            st.error("Please enter a name for the truck.")
        else:
            try:
                now = datetime.datetime.now().isoformat()

                # Supabase: Insert truck and get ID
                truck_response = supabase.from_('anticipated_trucks').insert({
                    'truck_name': truck_name,
                    'created_by': st.session_state.admin_username,
                    'created_at': now,
                    'day_of_week': selected_day
                }).execute()
                
                truck_id = truck_response.data[0]['id']

                barcodes = []
                items_to_insert = []

                # Reset batch_assigned_slots
                batch_assigned_slots = {}

                for item, qty in qtys.items():
                    for _ in range(qty):
                        slot = get_next_slot(item)
                        label = f"{item}_{slot}"
                        items_to_insert.append({
                            'truck_id': truck_id,
                            'item_code': item,
                            'slot': slot,
                            'barcode_label': label,
                            'status': 'pending'
                        })
                        png = generate_barcode_bytes(label)
                        barcodes.append((label, png))

                # Supabase: Bulk insert anticipated items
                supabase.from_('anticipated_items').insert(items_to_insert).execute()

                # NEW: Pass skip_slots to barcode PDF generator
                pdf_data = create_barcode_pdf(barcodes, skip_slots=skip_slots)

                st.download_button(
                    "Download 10x3 Sticker Sheet (PDF)",
                    data=pdf_data,
                    file_name=f"{truck_name}_barcodes.pdf",
                    mime="application/pdf",
                    key=f"download_{truck_name}_{datetime.datetime.now().timestamp()}"
                )

                st.success(f"Anticipated truck '{truck_name}' created for {selected_day}.")

            except Exception as e:
                st.error(f"Error creating truck: {e}")




    # ---------- Truck Summary Dashboard ----------
    st.subheader("Truck Summary Dashboard")
    # Supabase: Fetch all anticipated trucks
    trucks_data = supabase.from_('anticipated_trucks').select('id, truck_name, created_at, status').order('created_at', desc=True).execute().data
    trucks = pd.DataFrame(trucks_data)



    if not trucks.empty:
        t_choice = st.selectbox("Select truck to view", trucks.apply(lambda r: f"{r['id']} - {r['truck_name']} ({r['created_at']})", axis=1))
        t_id = int(t_choice.split(" - ")[0])

        # Supabase: Fetch anticipated items for the selected truck
        df_items_data = supabase.from_('anticipated_items').select('*').eq('truck_id', t_id).execute().data
        df_items = pd.DataFrame(df_items_data)

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
        truck_name = trucks[trucks['id'] == t_id]['truck_name'].iloc[0]

        with col2:
            if pending_count > 0:
                if st.button(f"Close {truck_name} (Mark Pending as Missing)", key=f"close_truck_{t_id}"):
                    now = datetime.datetime.now().isoformat()
                    
                    # Supabase: Get counts
                    total_items = len(df_items)
                    items_processed = len(df_items[df_items['status'] == 'scanned'])
                    
                    # Supabase: Update pending items as missing
                    supabase.from_('anticipated_items').update({'status': 'missing'}).eq('truck_id', t_id).eq('status', 'pending').execute()

                    # Supabase: Update truck status to closed
                    supabase.from_('anticipated_trucks').update({'status': 'closed'}).eq('id', t_id).execute()

                    # Supabase: Insert into analytics history
                    supabase.from_('analytics_history').insert({
                        'truck_id': t_id,
                        'closed_by': st.session_state.admin_username,
                        'closed_at': now,
                        'items_processed': items_processed,
                        'items_missing': pending_count,
                        'total_items': total_items
                    }).execute()
                    
                    st.success(f"Truck **{truck_name}** closed. Missing items marked.")
                    st.rerun()
            else:
                if st.button(f"Close {truck_name}", key=f"force_close_{t_id}"):
                    now = datetime.datetime.now().isoformat()
                    
                    # Supabase: Get counts
                    total_items = len(df_items)
                    items_processed = len(df_items[df_items['status'] == 'scanned'])
                    
                    # Supabase: Update truck status to closed
                    supabase.from_('anticipated_trucks').update({'status': 'closed'}).eq('id', t_id).execute()

                    # Supabase: Insert into analytics history
                    supabase.from_('analytics_history').insert({
                        'truck_id': t_id,
                        'closed_by': st.session_state.admin_username,
                        'closed_at': now,
                        'items_processed': items_processed,
                        'items_missing': 0,
                        'total_items': total_items
                    }).execute()
                    
                    st.success(f"Truck **{truck_name}** closed. All items already processed.")
                    st.rerun()

        # --- Delete Truck with Double Verification ---
        if "confirm_delete_truck" not in st.session_state:
            st.session_state.confirm_delete_truck = None

        if st.session_state.confirm_delete_truck != t_id:
            if st.button(f"Delete {trucks[trucks['id'] == t_id]['truck_name'].iloc[0]}", key=f"delete_{t_id}"):
                st.session_state.confirm_delete_truck = t_id
        else:
            truck_name = trucks[trucks['id'] == t_id]['truck_name'].iloc[0]
            st.warning(f"Are you sure you want to delete **{truck_name}** and ALL related data?")
            col1, col2 = st.columns(2)

            with col1:
                if st.button("Yes, Delete", key=f"yes_delete_{t_id}"):
                    # Delete all related data in proper order
                    supabase.from_("analytics_history").delete().eq("truck_id", t_id).execute()
                    supabase.from_("inventory").delete().eq("truck_id", t_id).execute()
                    supabase.from_("anticipated_items").delete().eq("truck_id", t_id).execute()
                    supabase.from_("anticipated_trucks").delete().eq("id", t_id).execute()

                    st.success(f"Truck **{truck_name}** and all related data were deleted.")
                    st.session_state.confirm_delete_truck = None
                    st.rerun()

            with col2:
                if st.button("Cancel", key=f"cancel_delete_{t_id}"):
                    st.session_state.confirm_delete_truck = None


    else:
        st.info("No anticipated trucks found.")

# ---------- Analytics Mode ---------------

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
    
    try:
        # Supabase: Fetch all anticipated trucks
        trucks_data = supabase.from_('anticipated_trucks').select('id, truck_name, created_by, created_at').order('created_at', desc=True).execute().data
        trucks = pd.DataFrame(trucks_data)
    except Exception as e:
        st.error(f"Error fetching truck data: {e}")
        trucks = pd.DataFrame()

    if not trucks.empty:
        truck_options = {f"{row['truck_name']} (ID {row['id']})": row['id'] for _, row in trucks.iterrows()}
        selected_truck_name = st.selectbox("Select a truck to view history:", list(truck_options.keys()))
        t_id = truck_options[selected_truck_name]

        try:
            # Supabase: Fetch truck creation info
            truck_info_data = supabase.from_('anticipated_trucks') \
                .select('created_by, created_at') \
                .eq('id', t_id) \
                .execute().data
            truck_info = truck_info_data[0] if truck_info_data else None
            created_by, created_at = (truck_info['created_by'], truck_info['created_at']) if truck_info else ("Unknown", "Unknown")

            # Supabase: Fetch users who scanned items for this truck
            scanned_by_data = supabase.from_('inventory') \
                .select('added_by') \
                .eq('truck_id', t_id) \
                .eq('status', 'in_stock') \
                .execute().data
            
            # Deduplicate users in Python
            scanned_users = {item['added_by'] for item in scanned_by_data} if scanned_by_data else set()
            scanned_by = ", ".join(scanned_users) if scanned_users else "No scans yet"

            # Supabase: Fetch truck closure info
            closed_info_data = supabase.from_('analytics_history') \
                .select('closed_by, closed_at') \
                .eq('truck_id', t_id) \
                .execute().data
            closed_info = closed_info_data[0] if closed_info_data else None
            closed_by, closed_at = (closed_info['closed_by'], closed_info['closed_at']) if closed_info else ("Not closed yet", "")

        except Exception as e:
            st.error(f"Error fetching truck history details: {e}")


        except Exception as e:
            st.error(f"Error fetching truck history details: {e}")
            created_by, created_at = "Error", "Error"
            scanned_by = "Error"
            closed_by, closed_at = "Error", ""

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

    try:
        # Supabase: Fetch all depleted items with timestamps
        depleted_items_data = supabase.from_('inventory').select('item_code, in_use_at, depleted_at').not_.is_('in_use_at', None).not_.is_('depleted_at', None).execute().data
        depleted_items = pd.DataFrame(depleted_items_data)

        if not depleted_items.empty:
            depleted_items['in_use_at'] = pd.to_datetime(depleted_items['in_use_at'])
            depleted_items['depleted_at'] = pd.to_datetime(depleted_items['depleted_at'])
            depleted_items['duration_days'] = (depleted_items['depleted_at'] - depleted_items['in_use_at']).dt.days
            
            avg_lifespan = depleted_items.groupby('item_code')['duration_days'].mean().reset_index()
            avg_lifespan.rename(columns={'duration_days': 'Average Lifespan (Days)'}, inplace=True)
            st.dataframe(avg_lifespan)
        else:
            st.info("Not enough data to calculate item lifespans.")
    except Exception as e:
        st.error(f"Error fetching item lifespan data: {e}")

    # --- Depletion Between Two Trucks ---
    st.markdown("---")
    st.subheader("Depletion Between Two Trucks")

    try:
        # Supabase: Fetch truck history from analytics_history table
        history_data = supabase.from_('analytics_history').select('truck_id, closed_at').order('closed_at').execute().data
        truck_history = pd.DataFrame(history_data)

        # Supabase: Fetch truck names to join with history
        truck_names_data = supabase.from_('anticipated_trucks').select('id, truck_name').execute().data
        truck_names = pd.DataFrame(truck_names_data)
        
        if not truck_history.empty and not truck_names.empty:
            # Merge to get truck names
            truck_history = truck_history.merge(truck_names, left_on='truck_id', right_on='id', how='left').drop('id', axis=1)
            truck_history['label'] = truck_history.apply(
                lambda r: f"{r['truck_name']} (ID {r['truck_id']}) - Closed {r['closed_at']}", axis=1
            )
    except Exception as e:
        st.error(f"Error fetching data for depletion analysis: {e}")
        truck_history = pd.DataFrame()


    if len(truck_history) >= 2:
        col1, col2 = st.columns(2)
        with col1:
            truck1_label = st.selectbox("Select First Truck:", truck_history['label'], index=len(truck_history)-2)
        with col2:
            truck2_label = st.selectbox("Select Second Truck:", truck_history['label'], index=len(truck_history)-1)

        truck1 = truck_history[truck_history['label'] == truck1_label].iloc[0]
        truck2 = truck_history[truck_history['label'] == truck2_label].iloc[0]

        if truck1['truck_id'] == truck2['truck_id']:
            st.warning("Please select two different trucks.")
        elif pd.to_datetime(truck1['closed_at']) > pd.to_datetime(truck2['closed_at']):
            st.error("The first truck's date must be before the second truck's date.")
        else:
            try:
                # Supabase: Fetch depleted items within the date range
                depletion_data = supabase.from_('inventory').select('item_code').gte('depleted_at', truck1['closed_at']).lte('depleted_at', truck2['closed_at']).execute().data
                depletion_df = pd.DataFrame(depletion_data)

                if not depletion_df.empty:
                    depletion_counts = depletion_df.groupby('item_code').size().reset_index(name='depleted_count').sort_values('depleted_count', ascending=False)
                    st.write(f"Items depleted between **{truck1['truck_name']}** and **{truck2['truck_name']}**:")
                    st.dataframe(depletion_counts)
                else:
                    st.info("No items were depleted between the selected trucks.")
            except Exception as e:
                st.error(f"Error calculating depletion: {e}")
    elif len(truck_history) == 1:
        st.info("Please close a second truck in Truck Management to see depletion analysis.")
    else:
        st.info("No truck history available yet.")



def messaging_mode():
    st.header("Admin Messaging")

    sections = ["user", "truck", "admin"]
    section = st.selectbox("Write message to:", sections)
    message = st.text_area("Message")

    if st.button("Send / Replace Message"):
        sender = st.session_state.get("admin_username", "Unknown Admin")

        # Delete old message for this section
        supabase.table("notifications").delete().eq("section", section).execute()

        # Insert new message with sender
        supabase.table("notifications").insert({
            "section": section,
            "message": message,
            "sender": sender
        }).execute()

        st.success(f"Message for {section} updated by {sender}!")


def get_section_message(section):
    res = supabase.table("notifications") \
        .select("message, sender") \
        .eq("section", section) \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()

    if res.data:
        return res.data[0]["message"], res.data[0]["sender"]
    return None, None


def manage_messages():
    st.subheader("Current Messages")
    res = supabase.table("notifications").select("*").execute()

    for row in res.data:
        st.write(f"**{row['section']}** → {row['message']} (from: {row['sender']})")
        if st.button(f"Delete {row['section']} ({row['id']})", key=f"delete_{row['id']}"):
            supabase.table("notifications").delete().eq("id", row["id"]).execute()
            st.success(f"Deleted message for {row['section']}")
            st.rerun()



# ----------------- Main Mode Selector -----------------

# Initialize session state
if 'admin_logged_in' not in st.session_state:
    st.session_state.admin_logged_in = False
if 'truck_logged_in' not in st.session_state:
    st.session_state.truck_logged_in = False
if "last_processed_scan" not in st.session_state:
    st.session_state.last_processed_scan = ""


mode = st.sidebar.selectbox("Select Mode", ["User Mode", "Truck Mode", "Admin Mode", "Truck Management", "Analytics Mode", "Notifications Mode"], index=0)

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
elif mode == "Notifications Mode":
    if st.session_state.admin_logged_in:  
        st.header("Notifications")

        tab1, tab2 = st.tabs(["Send / Replace Message", "Manage Messages"])

        with tab1:
            messaging_mode()  

        with tab2:
            manage_messages()
    else:
        st.warning("You must be logged in as an admin to view this page.")



