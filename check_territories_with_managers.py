#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Check which territories have managers
"""
import pyodbc

# Database connection
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)

cursor = conn.cursor()

# Get all territories with managers
query = """
    SELECT 
        sa.fSALESAREA,
        COUNT(DISTINCT ag.fID) as ManagerCount,
        STRING_AGG(CAST(ag.fCODE as VARCHAR(MAX)), ', ') as Managers
    FROM SALESAGENTAREAS sa
    INNER JOIN SALESAGENTS ag ON sa.fSALESAGENTID = ag.fID
    WHERE ag.fCLOSED = 0
    GROUP BY sa.fSALESAREA
    ORDER BY ManagerCount DESC
"""

cursor.execute(query)

print("🏢 Territories with managers:\n")
for row in cursor.fetchall():
    print(f"{row.fSALESAREA:10} | {row.ManagerCount:2} managers | {row.Managers}")

conn.close()
