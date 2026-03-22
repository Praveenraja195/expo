import pandas as pd
import random
import os

target_github = 'https://github.com/Praveenraja195'
target_linkedin = 'https://www.linkedin.com/in/praveenraja-s-5a9697357/'
user_reg = '731123104037'
user_name = "PRAVEENRAJA S"

def add_links(filepath):
    if not os.path.exists(filepath):
        print(f"File {filepath} not found.")
        return
        
    df = pd.read_csv(filepath)
    
    # Check if columns exist
    if 'linkedin' not in df.columns:
        df['linkedin'] = ''
    if 'github' not in df.columns:
        df['github'] = ''
        
    for index, row in df.iterrows():
        # Clean up row contents
        name_str = str(row.get('Name', '')).strip().upper()
        reg_no = str(row.get('Reg_No', '')).strip()
        
        # Determine if it's the user
        if reg_no == user_reg or name_str == user_name:
            df.at[index, 'github'] = target_github
            df.at[index, 'linkedin'] = target_linkedin
        else:
            first_name = name_str.split(' ')[0].lower() if name_str else f"student_{reg_no}"
            rand_id = random.randint(100, 999)
            df.at[index, 'github'] = f"https://github.com/{first_name}{rand_id}"
            df.at[index, 'linkedin'] = f"https://linkedin.com/in/{first_name}-{rand_id}/"

    df.to_csv(filepath, index=False)
    print(f"Updated {filepath} successfully.")

add_links('2nd_year_dataset.csv')
add_links('student_coach_dataset_final.csv')
