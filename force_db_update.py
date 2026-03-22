import pandas as pd
import sqlalchemy as sa
import os

DATABASE_URL = 'postgresql://genesis_user:genesis_pass@localhost:5432/genesis_db'
engine = sa.create_engine(DATABASE_URL)

print("📥 Seeding 3rd_year_students table from CSV...")
df_seed = pd.read_csv("student_coach_dataset_final.csv", dtype=str)
df_seed.columns = df_seed.columns.str.strip()
df_seed.to_sql('3rd_year_students', engine, if_exists='replace', index=False)
print(f"   Seeded {len(df_seed)} rows into 3rd_year_students.")

print("📥 Seeding 2nd_year_students table from CSV...")
df_seed2 = pd.read_csv("2nd_year_dataset.csv", dtype=str)
df_seed2.columns = df_seed2.columns.str.strip()
df_seed2.to_sql('2nd_year_students', engine, if_exists='replace', index=False)
print(f"   Seeded {len(df_seed2)} rows into 2nd_year_students.")
