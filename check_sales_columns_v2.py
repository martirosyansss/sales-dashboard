
from database import get_database

db = get_database()
conn = db.get_connection()
cursor = conn.cursor()

cursor.execute("SELECT TOP 1 * FROM SALES")
columns = [column[0] for column in cursor.description]
print(columns)

conn.close()
