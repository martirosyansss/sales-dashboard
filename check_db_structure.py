import pyodbc
import sys
from database import get_database

def check_database_structure():
    """Check and display the complete database structure"""
    
    db = get_database()
    
    if not db.connection:
        print("Failed to connect to database!")
        return None
    
    structure = {
        'tables': {},
        'relationships': []
    }
    
    # Get all tables
    print("Retrieving database structure...\n")
    
    cursor = db.connection.cursor()
    
    # Get all user tables
    cursor.execute("""
        SELECT TABLE_NAME 
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_CATALOG = 'SalesManagement'
        ORDER BY TABLE_NAME
    """)
    
    tables = [row[0] for row in cursor.fetchall()]
    print(f"Found {len(tables)} tables: {', '.join(tables)}\n")
    
    # For each table, get detailed information
    for table_name in tables:
        print(f"Analyzing table: {table_name}")
        structure['tables'][table_name] = {
            'columns': [],
            'primary_keys': [],
            'foreign_keys': [],
            'indexes': []
        }
        
        # Get column information
        cursor.execute("""
            SELECT 
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION,
                c.NUMERIC_SCALE,
                c.IS_NULLABLE,
                c.COLUMN_DEFAULT,
                COLUMNPROPERTY(OBJECT_ID(c.TABLE_SCHEMA + '.' + c.TABLE_NAME), c.COLUMN_NAME, 'IsIdentity') as IS_IDENTITY
            FROM INFORMATION_SCHEMA.COLUMNS c
            WHERE c.TABLE_NAME = ?
            ORDER BY c.ORDINAL_POSITION
        """, table_name)
        
        for row in cursor.fetchall():
            col_name, data_type, max_length, precision, scale, is_nullable, default_val, is_identity = row
            
            # Build full type string
            if data_type in ['nvarchar', 'varchar', 'char', 'nchar']:
                full_type = f"{data_type}({max_length if max_length else 'MAX'})"
            elif data_type in ['decimal', 'numeric']:
                full_type = f"{data_type}({precision}, {scale})"
            else:
                full_type = data_type
            
            structure['tables'][table_name]['columns'].append({
                'name': col_name,
                'type': full_type,
                'nullable': is_nullable == 'YES',
                'default': default_val,
                'identity': bool(is_identity)
            })
        
        # Get primary keys
        cursor.execute("""
            SELECT c.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE c 
                ON tc.CONSTRAINT_NAME = c.CONSTRAINT_NAME
            WHERE tc.TABLE_NAME = ? AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
        """, table_name)
        
        structure['tables'][table_name]['primary_keys'] = [row[0] for row in cursor.fetchall()]
        
        # Get foreign keys for this table
        cursor.execute("""
            SELECT 
                fk.name as FK_NAME,
                OBJECT_NAME(fk.parent_object_id) as TABLE_NAME,
                COL_NAME(fkc.parent_object_id, fkc.parent_column_id) as COLUMN_NAME,
                OBJECT_NAME(fk.referenced_object_id) as REFERENCED_TABLE,
                COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) as REFERENCED_COLUMN,
                fk.delete_referential_action_desc as DELETE_RULE,
                fk.update_referential_action_desc as UPDATE_RULE
            FROM sys.foreign_keys fk
            INNER JOIN sys.foreign_key_columns fkc 
                ON fk.object_id = fkc.constraint_object_id
            WHERE OBJECT_NAME(fk.parent_object_id) = ?
        """, table_name)
        
        for row in cursor.fetchall():
            fk_info = {
                'name': row[0],
                'column': row[2],
                'referenced_table': row[3],
                'referenced_column': row[4],
                'delete_rule': row[5],
                'update_rule': row[6]
            }
            structure['tables'][table_name]['foreign_keys'].append(fk_info)
            structure['relationships'].append({
                'from_table': table_name,
                'from_column': row[2],
                'to_table': row[3],
                'to_column': row[4],
                'constraint_name': row[0],
                'delete_rule': row[5],
                'update_rule': row[6]
            })
        
        # Get indexes
        cursor.execute("""
            SELECT 
                i.name as INDEX_NAME,
                i.type_desc as INDEX_TYPE,
                i.is_unique,
                COL_NAME(ic.object_id, ic.column_id) as COLUMN_NAME
            FROM sys.indexes i
            INNER JOIN sys.index_columns ic 
                ON i.object_id = ic.object_id AND i.index_id = ic.index_id
            WHERE OBJECT_NAME(i.object_id) = ? AND i.type > 0
            ORDER BY i.name, ic.key_ordinal
        """, table_name)
        
        indexes = {}
        for row in cursor.fetchall():
            idx_name = row[0]
            if idx_name not in indexes:
                indexes[idx_name] = {
                    'name': idx_name,
                    'type': row[1],
                    'unique': bool(row[2]),
                    'columns': []
                }
            indexes[idx_name]['columns'].append(row[3])
        
        structure['tables'][table_name]['indexes'] = list(indexes.values())
    
    cursor.close()
    
    # Print summary
    print("\n" + "="*60)
    print("DATABASE STRUCTURE SUMMARY")
    print("="*60)
    
    for table_name, table_info in structure['tables'].items():
        print(f"\n{table_name}:")
        print(f"  Columns: {len(table_info['columns'])}")
        print(f"  Primary Keys: {len(table_info['primary_keys'])}")
        print(f"  Foreign Keys: {len(table_info['foreign_keys'])}")
        print(f"  Indexes: {len(table_info['indexes'])}")
    
    print(f"\nTotal Relationships: {len(structure['relationships'])}")
    
    return structure

if __name__ == "__main__":
    structure = check_database_structure()
    
    if structure:
        print("\n✓ Database structure retrieved successfully!")
        print(f"✓ Found {len(structure['tables'])} tables")
        print(f"✓ Found {len(structure['relationships'])} relationships")
    else:
        print("\n✗ Failed to retrieve database structure")
        sys.exit(1)
