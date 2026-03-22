import os
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import pandas as pd
from openai import OpenAI
from google import genai
import json
import re
import logging
import sqlalchemy as sa

# Set up logging
logging.basicConfig(filename='server.log', level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s')

# --- 0. LOAD ENVIRONMENT VARIABLES ---
load_dotenv()  # This looks for the .env file in the same folder

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

@app.route('/healthz')
def health_check():
    return jsonify({"status": "healthy"}), 200

@app.route('/styles_v7.css')
def serve_css():
    return send_from_directory('.', 'styles.css')

@app.route('/main_v7.js')
def serve_js():
    return send_from_directory('.', 'main.js')

# --- 1. DATA LOAD FROM POSTGRESQL (CSVs only used for first-time seeding) ---
engine = None
df_3rd_year = pd.DataFrame()
df_2nd_year = pd.DataFrame()
df_students = pd.DataFrame()

try:
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://genesis_user:genesis_pass@db:5432/genesis_db')
    engine = sa.create_engine(DATABASE_URL)
    insp = sa.inspect(engine)

    # --- SEED: Only runs ONCE on first boot if tables do not exist yet ---
    if not insp.has_table('3rd_year_students'):
        print("📥 Seeding 3rd_year_students table from CSV (first boot only)...")
        df_seed = pd.read_csv("student_coach_dataset_final.csv", dtype=str)
        df_seed.columns = df_seed.columns.str.strip()
        df_seed.to_sql('3rd_year_students', engine, if_exists='replace', index=False)
        print(f"   Seeded {len(df_seed)} rows into 3rd_year_students.")

    if not insp.has_table('2nd_year_students'):
        print("📥 Seeding 2nd_year_students table from CSV (first boot only)...")
        df_seed2 = pd.read_csv("2nd_year_dataset.csv", dtype=str)
        df_seed2.columns = df_seed2.columns.str.strip()
        df_seed2.to_sql('2nd_year_students', engine, if_exists='replace', index=False)
        print(f"   Seeded {len(df_seed2)} rows into 2nd_year_students.")

    if not insp.has_table('scores'):
        with engine.begin() as conn:
            conn.execute(sa.text("""
                CREATE TABLE scores (
                    reg_no TEXT PRIMARY KEY,
                    name TEXT,
                    score INT,
                    total INT,
                    submitted_at TEXT
                )
            """))
        print("   Created scores table.")

    if not insp.has_table('messages'):
        with engine.begin() as conn:
            conn.execute(sa.text("""
                CREATE TABLE messages (
                    id SERIAL PRIMARY KEY,
                    sender_id TEXT,
                    receiver_id TEXT,
                    message TEXT,
                    timestamp TEXT,
                    is_read BOOLEAN DEFAULT FALSE
                )
            """))
        print("   Created messages table.")

    # --- PRIMARY DATA FETCH: Always from PostgreSQL ---
    df_3rd_year = pd.read_sql_table('3rd_year_students', engine).astype(str)
    df_2nd_year = pd.read_sql_table('2nd_year_students', engine).astype(str)
    df_students = pd.concat([df_3rd_year, df_2nd_year], ignore_index=True)

    print(f"✅ System Online (PostgreSQL): {len(df_3rd_year)} 3rd-year | {len(df_2nd_year)} 2nd-year | {len(df_students)} total students.")

except Exception as e:
    print(f"❌ CRITICAL: Cannot connect to PostgreSQL. Reason: {e}")
    print("   App is running but student data is unavailable. Fix the DB connection.")
    logging.error(f"PostgreSQL connection failed: {e}")

# --- Calculate class-wide averages for benchmarking ---
try:
    class_avg_cgpa = round(pd.to_numeric(df_students['CGPA'], errors='coerce').mean(), 2)
except:
    class_avg_cgpa = 7.5
try:
    att_col = next((c for c in df_students.columns if 'attendance' in c.lower()), None)
    class_avg_attendance = round(pd.to_numeric(df_students[att_col], errors='coerce').mean(), 1) if att_col else 80.0
except:
    class_avg_attendance = 80.0

# --- 2. API KEYS & LOAD BALANCER (From .env) ---
import random

# Gather all available Groq and Gemini keys
groq_keys = [os.getenv(f"GROQ_API_KEY_{i}") for i in range(1, 4) if os.getenv(f"GROQ_API_KEY_{i}")]
if os.getenv("GROQ_API_KEY"): groq_keys.append(os.getenv("GROQ_API_KEY"))

gemini_keys = [os.getenv(f"GEMINI_API_KEY_{i}") for i in range(1, 4) if os.getenv(f"GEMINI_API_KEY_{i}")]
if os.getenv("GEMINI_API_KEY"): gemini_keys.append(os.getenv("GEMINI_API_KEY"))

groq_clients = []
for key in groq_keys:
    from openai import OpenAI
    groq_clients.append(OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1"))

gemini_clients = []
for key in gemini_keys:
    gemini_clients.append(genai.Client(api_key=key))

def get_groq():
    return random.choice(groq_clients) if groq_clients else None

def get_gemini():
    return random.choice(gemini_clients) if gemini_clients else None

# Initialize JSON-backed/DB-backed skill-check scoreboard
SCORES_FILE = 'scores.json'
def load_scores():
    import json, os
    if "engine" in globals() and engine is not None:
        try:
            df_scores = pd.read_sql_table('scores', engine).astype(str)
            res = {}
            for _, r in df_scores.iterrows():
                res[r['reg_no']] = {
                    'name': r['name'],
                    'score': int(float(r['score'])),
                    'total': int(float(r['total'])),
                    'submitted_at': r['submitted_at']
                }
            return res
        except Exception as e:
            logging.error(f"DB Load Error: {e}")
    # Fallback to JSON
    if os.path.exists(SCORES_FILE):
        try:
            with open(SCORES_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading scores: {e}")
    return {}

def save_scores(data):
    import json
    with open(SCORES_FILE, 'w') as f:
        json.dump(data, f)

score_board = load_scores()

@app.route('/spotlight', methods=['POST'])
def get_spotlight():
    """Find the top student based on CGPA dynamically per dataset."""
    try:
        data = request.json or {}
        staff_id = data.get('staff_id', '').lower()

        # Determine context dataset
        if staff_id == 'thenmozhi':
            context_df = df_2nd_year
        elif staff_id == 'kavidha' or staff_id == 'admin':
            context_df = df_students
        else:
            context_df = df_3rd_year

        df = context_df.copy()

        # Dynamically find highest CGPA
        df['CGPA_num'] = pd.to_numeric(df['CGPA'], errors='coerce')
        df_sorted = df.dropna(subset=['CGPA_num']).sort_values(by='CGPA_num', ascending=False)

        if staff_id == 'kavidha' or staff_id == 'admin':
            user_row = df[df['Reg_No'].astype(str) == '731123104037']
            if not user_row.empty:
                top = user_row.iloc[0]
            elif not df_sorted.empty:
                top = df_sorted.iloc[0]
            elif not df.empty:
                top = df.iloc[0]
            else:
                return jsonify({"status": "error", "message": "No data available."})
        else:
            if not df_sorted.empty:
                top = df_sorted.iloc[0]
            elif not df.empty:
                top = df.iloc[0]
            else:
                return jsonify({"status": "error", "message": "No data available."})

        def safe(v):
            if v is None: return 'N/A'
            s = str(v).strip()
            return 'N/A' if s in ('', 'nan', 'NaN', 'None') else s

        possible_goal_cols = ['Target_Career_Role', 'Target_Career_Goal', 'Role']
        goal_col = next((c for c in possible_goal_cols if c in df.columns), None)

        return jsonify({
            "status": "success",
            "student": {
                "Name": safe(top.get('Name')),
                "CGPA": safe(top.get('CGPA')),
                "Projects": safe(top.get('Completed_Projects_Count')),
                "Career_Goal": safe(top.get(goal_col)) if goal_col else "N/A",
                "Skills": safe(top.get('Technical_Skills_Known'))
            }
        })
    except Exception as e:
        logging.error(f"Spotlight Error: {e}")
        return jsonify({"status": "error", "message": str(e)})

# --- 3. STAFF PORTAL ROUTE (/chat) ---
@app.route('/chat', methods=['POST'])
def staff_chatbot():
    try:
        data = request.json
        user_msg = data.get('message')
        staff_id = data.get('staff_id', '').lower()

        # Determine context dataset
        if staff_id == 'thenmozhi':
            context_df = df_2nd_year
        elif staff_id == 'kavidha' or staff_id == 'admin':
            context_df = df_students  # combined
        else:
            context_df = df_3rd_year  # default to 3rd year

        possible_goal_cols = ['Target_Career_Role', 'Target_Career_Goal', 'Role']
        goal_col = next((c for c in possible_goal_cols if c in context_df.columns), None)

        cols_to_show = ['Name', 'Reg_No', 'CGPA']
        if goal_col:
            cols_to_show.append(goal_col)

        # Sending the specific class data for accurate analysis
        class_summary = context_df[cols_to_show].to_string()

        prompt = f"""
        You are an Academic Data Analyst for a college class.
        CLASS DATA SUMMARY:
        {class_summary}

        STRICT FORMATTING RULES - YOU MUST FOLLOW THESE:
        - Respond in plain text ONLY. No markdown whatsoever.
        - Do NOT use asterisks (*), double asterisks (**), hashes (#), backticks, underscores for formatting.
        - Do NOT use markdown tables (|). Use simple numbered lists instead.
        - Do NOT use bullet points starting with * or -. Use numbers (1. 2. 3.) instead.
        - Write in clear, natural English sentences.
        - For charts, provide explanation and ONE JSON block only: {{"is_chart": true, "chart_type": "bar", "title": "...", "labels": [], "data": []}}
        """

        try:
            res = get_groq().chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": user_msg}]
            )
            reply = res.choices[0].message.content
        except Exception as e:
            logging.error(f"Groq Error in staff: {e}. Falling back to Gemini.")
            try:
                res = get_gemini().models.generate_content(model='gemini-2.0-flash', contents=f"{prompt}\n{user_msg}")
                reply = res.text
            except Exception as e2:
                logging.error(f"Gemini Error in staff: {e2}")
                return jsonify({"status": "error", "message": "All AI models failed", "reply": f"AI Error: {str(e2)}"})

        # 🚀 ROBUST JSON EXTRACTION: Find the first valid JSON block { ... }
        try:
            # Using a more careful regex to find the first '{' to its matching '}'
            # This is simpler and less prone to greedy capture across multiple blocks
            matches = re.findall(r'\{.*?\}', reply, re.DOTALL)
            for m in matches:
                try:
                    chart_json = json.loads(m)
                    if chart_json.get("is_chart"):
                        return jsonify({"status": "success", "type": "chart", "chart_data": chart_json, "reply": reply})
                except:
                    continue

            return jsonify({"status": "success", "type": "text", "reply": reply})
        except Exception as e:
            logging.error(f"Groq/Gemini Error in staff: {e}")
            return jsonify({"status": "success", "type": "text", "reply": f"AI Error: {str(e)}"})

    except Exception as e:
        logging.error(f"Staff Route Critical Error: {e}")
        return jsonify({"status": "error", "message": str(e), "reply": "Internal Server Error. Check server.log."})

# --- 4. STUDENT PORTAL ROUTE (/student/chat) ---
@app.route('/student/chat', methods=['POST'])
def student_chatbot():
    try:
        data = request.json
        reg_no = str(data.get('reg_no')).strip()
        user_msg = data.get('message')

        student_row = df_students[df_students['Reg_No'] == reg_no]
        if student_row.empty:
            return jsonify({"status": "error", "message": "ID not found"}), 404

        full_record = student_row.to_dict(orient='records')[0]

        # Injects the FULL row for deep analysis
        prompt = f"""You are the Personal AI Mentor for {full_record.get('Name')}.
        Student Data: {json.dumps(full_record)}

        STRICT FORMATTING RULES - YOU MUST FOLLOW THESE:
        - Respond in plain text ONLY. No markdown whatsoever.
        - Do NOT use asterisks (*), double asterisks (**), hashes (#), backticks, underscores for formatting.
        - Do NOT use markdown tables (|). Present data in simple numbered sentences.
        - Do NOT use bullet points with * or -. Use numbers like 1. 2. 3. instead.
        - Write naturally as a friendly mentor speaking to a student.
        - Keep responses concise and actionable."""

        try:
            res = get_groq().chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": user_msg}]
            )
            reply = res.choices[0].message.content
        except Exception as e:
            logging.error(f"Groq/Gemini Error in student: {e}")
            res = get_gemini().models.generate_content(model='gemini-2.0-flash', contents=f"{prompt}\n{user_msg}")
            reply = res.text

        return jsonify({"status": "success", "reply": reply})
    except Exception as e:
        logging.error(f"Student Route Critical Error: {e}")
        return jsonify({"status": "error", "message": str(e)})

# --- 5. STUDENT LOGIN ROUTE ---
@app.route('/student/login', methods=['POST'])
def student_login():
    try:
        data = request.json
        reg_no = str(data.get('reg_no')).strip()
        password = str(data.get('password')).strip()

        student_row = df_students[df_students['Reg_No'] == reg_no]
        if student_row.empty:
            return jsonify({"status": "error", "message": "Invalid Registration Number"}), 404

        # Get profile data
        student_data = student_row.to_dict(orient='records')[0]

        # Verify password (DOB)
        if str(student_data.get('DOB')).strip() != password:
            return jsonify({"status": "error", "message": "Incorrect Password (Hint: DOB)"}), 401

        # Determine year based on which dataset they appear in
        is_3rd = not df_3rd_year[df_3rd_year['Reg_No'] == reg_no].empty
        is_2nd = not df_2nd_year[df_2nd_year['Reg_No'] == reg_no].empty
        year_str = "3rd Year" if is_3rd else ("2nd Year" if is_2nd else "Unknown Year")

        # Return necessary fields for the dashboard
        return jsonify({
            "status": "success",
            "profile": {
                "name": student_data.get('Name'),
                "reg_no": student_data.get('Reg_No'),
                "cgpa": student_data.get('CGPA'),
                "attendance": student_data.get('Sem6_Current_Attendance_%'),
                "projects": student_data.get('Completed_Projects_Count'),
                "email": student_data.get('Email'),
                "dept": "Computer Science & Engineering",
                "year": year_str,
                "skills": student_data.get('Technical_Skills_Known'),
                "career_goal": student_data.get('Target_Career_Goal'),
                "linkedin": student_data.get('linkedin'),
                "github": student_data.get('github'),
                "aptitude_score": student_data.get('Aptitude_Test_Score_Avg'),
                "interview_rating": student_data.get('Mock_Interview_Rating'),
                "arrears": student_data.get('Total_Arrears_History'),
                "favorite_subject": student_data.get('Favorite_Subject'),
                "area_for_improvement": student_data.get('Area_for_Improvement'),
                "class_avg_cgpa": class_avg_cgpa,
                "class_avg_attendance": class_avg_attendance,
                "gpa_history": {
                    "Sem1": student_data.get('Sem1_GPA'),
                    "Sem2": student_data.get('Sem2_GPA'),
                    "Sem3": student_data.get('Sem3_GPA'),
                    "Sem4": student_data.get('Sem4_GPA'),
                    "Sem5": student_data.get('Sem5_GPA')
                }
            }
        })
    except Exception as e:
        print(f"❌ Login Error: {e}")
        return jsonify({"status": "error", "message": str(e)})

# --- 6. SKILL-CHECK SCORE SUBMISSION ---
@app.route('/student/submit_score', methods=['POST'])
def submit_score():
    try:
        data = request.json
        reg_no = str(data.get('reg_no', 'unknown')).strip()
        score  = data.get('score', 0)
        total  = data.get('total', 10)
        name   = data.get('name', 'Unknown')
        from datetime import datetime
        submitted_at = datetime.now().strftime('%H:%M, %d %b')
        score_board[reg_no] = {
            'name': name,
            'score': score,
            'total': total,
            'submitted_at': submitted_at
        }
        if "engine" in globals() and engine is not None:
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("""
                        INSERT INTO scores (reg_no, name, score, total, submitted_at)
                        VALUES (:r, :n, :s, :t, :a)
                        ON CONFLICT (reg_no) DO UPDATE SET
                            score = EXCLUDED.score, total = EXCLUDED.total, submitted_at = EXCLUDED.submitted_at
                    """), {"r": reg_no, "n": name, "s": score, "t": total, "a": submitted_at})
            except Exception as dbe:
                logging.error(f"DB Error on upload: {dbe}")
                save_scores(score_board)
        else:
            save_scores(score_board)
        logging.info(f"[SkillCheck] Student {reg_no} ({name}) scored {score}/{total}")
        return jsonify({"status": "success", "message": f"Score {score}/{total} recorded."})
    except Exception as e:
        logging.error(f"submit_score Error: {e}")
        return jsonify({"status": "error", "message": str(e)})

# --- 7. ADMIN SCORES ENDPOINT ---
@app.route('/admin/scores', methods=['POST'])
def get_scores():
    """Returns students with their skill-check submission status based on role."""
    all_students_status = []

    data = request.json or {}
    staff_id = data.get('staff_id', '').lower()

    if staff_id == 'thenmozhi':
        context_df = df_2nd_year
    elif staff_id == 'kavidha' or staff_id == 'admin':
        context_df = df_students
    else:
        context_df = df_3rd_year

    if context_df is not None and not context_df.empty:
        for _, row in context_df.iterrows():
            reg_no = str(row['Reg_No']).strip()
            name = str(row['Name']).strip()

            if reg_no in score_board:
                s = score_board[reg_no]
                all_students_status.append({
                    "reg_no": reg_no,
                    "name": name,
                    "submitted": True,
                    "score": s.get("score"),
                    "total": s.get("total"),
                    "submitted_at": s.get("submitted_at")
                })
            else:
                all_students_status.append({
                    "reg_no": reg_no,
                    "name": name,
                    "submitted": False
                })

    total_students = len(context_df) if context_df is not None else 0
    # calculate total submitted from this bounded set
    total_submitted = sum(1 for s in all_students_status if s['submitted'])
    return jsonify({
        "status": "success",
        "total_students": total_students,
        "submitted": total_submitted,
        "not_submitted": total_students - total_submitted,
        "scores": all_students_status
    })

# --- 8. EXCEL UPLOAD & CHAT (FOR SPECIFIC FACULTY) ---
@app.route('/staff/upload_excel', methods=['POST'])
def upload_excel():
    try:
        staff_id = request.form.get('staff_id', '').lower()
        if not staff_id:
            return jsonify({"status": "error", "message": "staff_id missing"})
            
        file = request.files.get('file')
        if not file:
            return jsonify({"status": "error", "message": "No file uploaded"})
            
        filename = file.filename
        if not filename.endswith(('.xlsx', '.xls')):
            return jsonify({"status": "error", "message": "Only Excel files supported"})
            
        try:
            df = pd.read_excel(file)
            # converting timezone-unaware dates or cleaning data
            df = df.astype(str) 
            json_data = df.to_json(orient='records')
            
            from datetime import datetime
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # --- NEW FEATURE: SAVE TO CSV PER FACULTY ---
            try:
                # Create directory under static so it maps to user's local volume
                safe_staff_id = re.sub(r'[^a-zA-Z0-9]', '_', staff_id)
                folder_path = os.path.join("static", "staff_archives", safe_staff_id)
                os.makedirs(folder_path, exist_ok=True)
                
                time_suffix = datetime.now().strftime('%Y%m%d_%H%M%S')
                base_name = os.path.splitext(filename)[0]
                csv_filename = f"{base_name}_{time_suffix}.csv"
                
                csv_path = os.path.join(folder_path, csv_filename)
                df.to_csv(csv_path, index=False)
                logging.info(f"Saved CSV archive for {staff_id} -> {csv_path}")
            except Exception as ex:
                logging.error(f"Failed to save CSV locally: {ex}")
            # --------------------------------------------
            
            if engine is not None:
                with engine.begin() as conn:
                    conn.execute(sa.text("""
                        CREATE TABLE IF NOT EXISTS staff_excel_files (
                            id SERIAL PRIMARY KEY,
                            staff_id TEXT,
                            filename TEXT,
                            file_data TEXT,
                            uploaded_at TEXT
                        )
                    """))
                    conn.execute(sa.text("""
                        INSERT INTO staff_excel_files (staff_id, filename, file_data, uploaded_at)
                        VALUES (:s, :fn, :d, :a)
                    """), {"s": staff_id, "fn": filename, "d": json_data, "a": now_str})
                    
            return jsonify({"status": "success", "message": f"{filename} uploaded and saved to DB."})
        except Exception as e:
            logging.error(f"Excel processing error: {e}")
            return jsonify({"status": "error", "message": f"Error parsing Excel: {e}"})
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/staff/list_excel', methods=['POST'])
def list_excel():
    try:
        data = request.json
        staff_id = data.get('staff_id', '').lower()
        
        if engine is not None:
            with engine.connect() as conn:
                res = conn.execute(sa.text("""
                    SELECT id, filename, uploaded_at FROM staff_excel_files 
                    WHERE staff_id = :s ORDER BY id DESC
                """), {"s": staff_id}).fetchall()
        else:
            res = []
            
        files = [{"id": r[0], "filename": r[1], "uploaded_at": r[2]} for r in res]
        return jsonify({"status": "success", "files": files})
    except Exception as e:
        # Table might not exist yet if no uploads
        return jsonify({"status": "success", "files": []})

@app.route('/staff/excel_chat', methods=['POST'])
def excel_chat():
    try:
        data = request.json
        staff_id = data.get('staff_id', '').lower()
        question = data.get('question')
        
        if engine is not None:
            with engine.connect() as conn:
                # get up to last 3 uploaded files for this user to append to context
                res = conn.execute(sa.text("""
                    SELECT filename, file_data FROM staff_excel_files 
                    WHERE staff_id = :s ORDER BY id DESC LIMIT 3
                """), {"s": staff_id}).fetchall()
        else:
            res = []
            
        if not res:
            return jsonify({"status": "error", "message": "No Excel files found in the database. Please upload one first."})
            
        context_str = ""
        for r in res:
            fn = r[0]
            d = r[1]
            try:
                records = json.loads(d)
                df_temp = pd.DataFrame(records)
                context_str += f"\\n--- DATA FROM FILE: {fn} ---\\n"
                # Keep it text-friendly, take full df if small, else head to bound size
                if len(df_temp) > 1000:
                    df_temp = df_temp.head(1000)
                context_str += df_temp.to_string(index=False)
                context_str += "\\n"
            except:
                pass
                
        prompt = f"""You are a Data Analyst Assistant for faculty.
        The user has uploaded Excel file(s) safely stored in our PostgreSQL Database.
        Here is the extracted data from their uploaded file(s):
        {context_str}
        
        STRICT FORMATTING RULES:
        - Respond in plain text ONLY. No markdown whatsoever.
        - Do NOT use markdown tables or hashes or asterisks.
        - Answer the user's question clearly based ONLY on the provided data above.
        - Be professional and helpful. Note patterns, summarize details if asked.
        - For charts, provide explanation and ONE JSON block only: {{"is_chart": true, "chart_type": "bar", "title": "...", "labels": [], "data": []}}
        """
        
        try:
            chat_res = get_groq().chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": question}]
            )
            reply = chat_res.choices[0].message.content
        except Exception as e:
            logging.error(f"Groq API error in excel chat: {e}")
            try:
                chat_res = get_gemini().models.generate_content(model='gemini-2.0-flash', contents=f"{prompt}\\nUser Question: {question}")
                reply = chat_res.text
            except Exception as e2:
                logging.error(f"Gemini API error in excel chat: {e2}")
                return jsonify({"status": "error", "message": "All AI models failed at the moment.", "reply": f"Error: {e2}"})
                
        # Extract chart data if present (same logic as Class Insight Engine)
        try:
            matches = re.findall(r'\{.*?\}', reply, re.DOTALL)
            for m in matches:
                try:
                    chart_json = json.loads(m)
                    if chart_json.get("is_chart"):
                        return jsonify({"status": "success", "type": "chart", "chart_data": chart_json, "reply": reply})
                except:
                    continue
            return jsonify({"status": "success", "type": "text", "reply": reply})
        except Exception as e:
            logging.error(f"Groq/Gemini JSON extraction error: {e}")
            return jsonify({"status": "success", "type": "text", "reply": reply})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# --- 9. INTER-PORTAL CHAT SYSTEM ---
@app.route('/messages/send', methods=['POST'])
def send_message():
    try:
        data = request.json
        sender = data.get('sender_id')
        receiver = data.get('receiver_id')
        msg = data.get('message')
        if not sender or not receiver or not msg:
            return jsonify({"status": "error", "message": "Missing fields."}), 400
        
        from datetime import datetime
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if engine is not None:
            with engine.begin() as conn:
                conn.execute(sa.text("""
                    INSERT INTO messages (sender_id, receiver_id, message, timestamp, is_read)
                    VALUES (:s, :r, :m, :t, :rd)
                """), {"s": sender, "r": receiver, "m": msg, "t": now_str, "rd": False})
        return jsonify({"status": "success", "message": "Sent."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/messages/history', methods=['POST'])
def get_chat_history():
    try:
        data = request.json
        user1 = data.get('user1')
        user2 = data.get('user2')
        if not user1 or not user2:
            return jsonify({"status": "error", "message": "Missing users."}), 400
        
        if engine is not None:
            with engine.connect() as conn:
                # Mark as read
                with conn.begin():
                    conn.execute(sa.text("""
                        UPDATE messages SET is_read = TRUE 
                        WHERE sender_id = :s AND receiver_id = :r AND is_read = FALSE
                    """), {"s": user2, "r": user1})
                
                # Fetch history
                res = conn.execute(sa.text("""
                    SELECT id, sender_id, receiver_id, message, timestamp, is_read 
                    FROM messages 
                    WHERE (sender_id = :u1 AND receiver_id = :u2)
                       OR (sender_id = :u2 AND receiver_id = :u1)
                    ORDER BY id ASC
                """), {"u1": user1, "u2": user2}).fetchall()
        else:
            res = []
            
        messages = [{"id": r[0], "sender_id": r[1], "receiver_id": r[2], "message": r[3], "timestamp": r[4], "is_read": r[5]} for r in res]
        return jsonify({"status": "success", "messages": messages})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/messages/clear', methods=['POST'])
def clear_chat():
    try:
        user1 = str(request.json.get('user1', '')).lower()
        user2 = str(request.json.get('user2', '')).lower()

        if engine is not None:
            with engine.connect() as conn:
                conn.execute(sa.text("""
                    DELETE FROM messages 
                    WHERE (sender_id = :u1 AND receiver_id = :u2)
                       OR (sender_id = :u2 AND receiver_id = :u1)
                """), {"u1": user1, "u2": user2})
                return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "Database disconnected"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/messages/contacts', methods=['POST'])
def get_contacts():
    # Fetch active conversations + known staff accounts
    try:
        my_id = str(request.json.get('user_id', '')).lower()
        if not my_id:
            return jsonify({"status": "error", "message": "Unknown user ID"}), 400

        contacts = []

        if engine is not None:
            with engine.connect() as conn:
                # Find all distinct users we've chatted with
                res = conn.execute(sa.text("""
                    SELECT DISTINCT 
                      CASE WHEN sender_id = :m THEN receiver_id ELSE sender_id END as other_id 
                    FROM messages 
                    WHERE sender_id = :m OR receiver_id = :m
                """), {"m": my_id}).fetchall()
                
                chat_partners = [r[0] for r in res]
                
                # Also find unread message counts grouped by sender
                unreads = conn.execute(sa.text("""
                    SELECT sender_id, COUNT(*) 
                    FROM messages 
                    WHERE receiver_id = :m AND is_read = FALSE
                    GROUP BY sender_id
                """), {"m": my_id}).fetchall()
                unread_map = {r[0]: r[1] for r in unreads}

                contact_ids = [c["id"] for c in contacts]
                
                for p in chat_partners:
                    if p not in contact_ids:
                        name = p
                        role = "User"
                        
                        # Staff mapping
                        staff_map = {
                            "admin": {"name": "System Admin", "role": "HOD"},
                            "kavidha": {"name": "Dr. A. Kavidha", "role": "HOD"},
                            "vasuki": {"name": "Mrs. N. Vasuki", "role": "Faculty / Class Advisor"},
                            "thenmozhi": {"name": "Dr. D. S. Thenmozhi", "role": "Faculty"}
                        }

                        if p in staff_map:
                            name = staff_map[p]["name"]
                            role = staff_map[p]["role"]
                        else:
                            try:
                                # Native SQL lookups instead of local dataframes
                                fetched_name = conn.execute(sa.text('SELECT "Name" FROM "3rd_year_students" WHERE "Reg_No" = :p'), {"p": p}).scalar()
                                if not fetched_name:
                                    fetched_name = conn.execute(sa.text('SELECT "Name" FROM "2nd_year_students" WHERE "Reg_No" = :p'), {"p": p}).scalar()
                                
                                if fetched_name:
                                    name = str(fetched_name).strip()
                                    role = "Student"
                            except Exception as inner_e:
                                print(f"Error fetching name for {p}: {inner_e}")
                                # fallback to ID
                                name = p
                                role = "Staff/User"
                        
                        contacts.append({
                            "id": p,
                            "name": name,
                            "role": role,
                            "unread": unread_map.get(p, 0),
                            "last_message": None,
                            "last_time": None
                        })
                    else:
                        # Just update unread count for existing
                        for c in contacts:
                            if c["id"] == p:
                                c["unread"] = unread_map.get(p, 0)
        
        # We can sort by unread count descending, then name
        contacts.sort(key=lambda x: (-x["unread"], x["name"]))
                
        return jsonify({"status": "success", "contacts": contacts})
    except Exception as e:
        print("Contacts error:", e)
        return jsonify({"status": "error", "message": str(e)})

@app.route('/messages/unread_total', methods=['POST'])
def get_unread_total():
    try:
        my_id = str(request.json.get('user_id', '')).lower()
        if engine is not None:
            with engine.connect() as conn:
                res = conn.execute(sa.text("""
                    SELECT COUNT(*) FROM messages WHERE receiver_id = :m AND is_read = FALSE
                """), {"m": my_id}).scalar()
                return jsonify({"status": "success", "count": res})
        return jsonify({"status": "success", "count": 0})
    except Exception as e:
         return jsonify({"status": "error", "message": str(e)})

@app.route('/api/students/list', methods=['GET'])
def get_student_list():
    try:
        global df_students
        if df_students is None or df_students.empty:
            return jsonify({"status": "error", "message": "Dataset not loaded"})
            
        students = []
        for _, row in df_students.iterrows():
            students.append({
                "reg_no": str(row.get("Reg_No", "")),
                "name": str(row.get("Name", "Unknown")),
                "email": str(row.get("Email", ""))
            })
            
        students.sort(key=lambda x: x["name"])
        return jsonify({"status": "success", "students": students})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/ml/clusters', methods=['GET'])
def get_ml_clusters():
    try:
        from sklearn.cluster import KMeans
        import numpy as np

        global df_students
        if df_students is None or df_students.empty:
            return jsonify({"status": "error", "message": "Dataset not initialized"})
            
        data_rows = []
        for idx, row in df_students.iterrows():
            reg_no = str(row.get('Reg_No', f"U{idx}"))
            
            try: cgpa = float(row.get('CGPA', 7.0))
            except: cgpa = 7.0
            
            try: apt = float(row.get('Aptitude_Test_Score_Avg', 70.0))
            except: apt = 70.0
            
            try: interview = float(row.get('Mock_Interview_Rating', 7.0))
            except: interview = 7.0
            
            core = min(100.0, cgpa * 10)
            comm = min(100.0, interview * 10)
            
            # Deterministically synthesize coding/logic based on Reg_No so it remains constant per student
            # We slice the last 8 digits because 12-digit Reg_Nos exceed Numpy's 2**32-1 seed limit!
            safe_seed = int(reg_no[-8:]) if reg_no[-8:].isdigit() else 42
            np.random.seed(safe_seed)
            coding = min(100.0, core + np.random.randint(-15, 20))
            logic = min(100.0, apt + np.random.randint(-15, 15))
            
            data_rows.append({
                'reg_no': reg_no,
                'coding': coding,
                'logic': logic,
                'aptitude': apt,
                'communication': comm,
                'core': core
            })
            
        df = pd.DataFrame(data_rows)
        X = df[['coding', 'logic', 'aptitude', 'communication', 'core']].fillna(0)
        
        kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
        df['cluster'] = kmeans.fit_predict(X)
        centroids = kmeans.cluster_centers_
        
        clusters = []
        for i in range(3):
            centroid = centroids[i]
            traits = ['Coding', 'Logic', 'Aptitude', 'Communication', 'Core']
            max_trait_idx = np.argmax(centroid)
            dominant_trait = traits[max_trait_idx]
            
            student_count = int((df['cluster'] == i).sum())
            students = df[df['cluster'] == i]['reg_no'].tolist()
            
            scores = sorted([(traits[idx], centroid[idx]) for idx in range(5)], key=lambda x: x[1], reverse=True)
            top_trait = scores[0][0]
            second_trait = scores[1][0]
            
            if top_trait == 'Coding':
                archetype = "The Code Architects"
            elif top_trait == 'Logic':
                archetype = "The Logic Analysts"
            elif top_trait == 'Aptitude':
                archetype = "The Quantitative Thinkers"
            elif top_trait == 'Communication':
                archetype = "The Communicators"
            else:
                archetype = "The Core Specialists"
                
            clusters.append({
                "cluster_id": i,
                "name": archetype,
                "traits_profile": f"Dominant in {top_trait} & {second_trait}",
                "centroid": [float(val) for val in centroid],
                "student_count": student_count,
                "students": students
            })
            
        return jsonify({"status": "success", "clusters": clusters})

    except Exception as e:
        print("ML Cluster Error:", e)
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
