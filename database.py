import pyodbc
from typing import Optional, List, Tuple
import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Manages SQL Server database connections for Sales Management System"""
    
    def __init__(self, server: str = None, database: str = None, 
                 username: str = None, password: str = None):
        """
        Initialize database connection parameters
        
        Args:
            server: SQL Server instance (default: from env or localhost)
            database: Database name (default: from env or SalesManagement)
            username: SQL Server username (default: from env or sa)
            password: SQL Server password (default: from env or Aa123456)
        """
        self.server = server or os.getenv('DB_SERVER', 'localhost')
        self.database = database or os.getenv('DB_NAME', 'SalesManagement')
        self.username = username or os.getenv('DB_USER', 'sa')
        self.password = password or os.getenv('DB_PASSWORD', 'Aa123456')
        self.connection = None
        
    def connect(self) -> bool:
        """
        Establish connection to SQL Server database
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            # Connection string for SQL Server
            connection_string = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={self.server};"
                f"DATABASE={self.database};"
                f"UID={self.username};"
                f"PWD={self.password};"
                f"TrustServerCertificate=yes;"
            )
            
            self.connection = pyodbc.connect(connection_string)
            logger.info(f"Successfully connected to database: {self.database}")
            return True
            
        except pyodbc.Error as e:
            logger.error(f"Database connection error: {str(e)}")
            return False

    def ensure_database_exists(self) -> bool:
        """
        Create database if it doesn't exist by connecting to the master database.
        
        Returns:
            bool: True if database exists or was created successfully.
        """
        try:
            # Connect to master database to check for and potentially create the new database
            connection_string = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={self.server};"
                f"DATABASE=master;"
                f"UID={self.username};"
                f"PWD={self.password};"
                f"TrustServerCertificate=yes;"
            )
            
            master_conn = pyodbc.connect(connection_string, autocommit=True)
            cursor = master_conn.cursor()
            
            # Check if database exists
            cursor.execute(f"SELECT database_id FROM sys.databases WHERE name = '{self.database}'")
            if cursor.fetchone() is None:
                logger.info(f"Database '{self.database}' not found. Attempting to create it.")
                cursor.execute(f"CREATE DATABASE [{self.database}]")
                logger.info(f"Database '{self.database}' created successfully.")
            else:
                logger.info(f"Database '{self.database}' already exists.")
            
            cursor.close()
            master_conn.close()
            return True
            
        except pyodbc.Error as e:
            logger.error(f"Error during database existence check/creation: {str(e)}")
            return False
    
    def disconnect(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            logger.info("Database connection closed")
    
    def execute_query(self, query: str, params: Optional[Tuple] = None) -> Optional[List[Tuple]]:
        """
        Execute a SELECT query and return results
        
        Args:
            query: SQL SELECT query
            params: Optional tuple of query parameters
            
        Returns:
            List of tuples containing query results, or None on error
        """
        if not self.connection:
            logger.error("Cannot execute query, no database connection.")
            return None
        try:
            cursor = self.connection.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            results = cursor.fetchall()
            cursor.close()
            return results
            
        except pyodbc.Error as e:
            logger.error(f"Query execution error: {str(e)}")
            return None
    
    def execute_non_query(self, query: str, params: Optional[Tuple] = None) -> bool:
        """
        Execute an INSERT, UPDATE, or DELETE query
        
        Args:
            query: SQL query (INSERT, UPDATE, DELETE)
            params: Optional tuple of query parameters
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.connection:
            logger.error("Cannot execute non-query, no database connection.")
            return False
        try:
            cursor = self.connection.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            self.connection.commit()
            cursor.close()
            logger.info("Query executed successfully")
            return True
            
        except pyodbc.Error as e:
            logger.error(f"Non-query execution error: {str(e)}")
            self.connection.rollback()
            return False
    
    def initialize_tables(self) -> bool:
        """
        Create necessary tables for Sales Management System
        
        Returns:
            bool: True if tables created successfully
        """
        if not self.connection:
            logger.error("Cannot initialize tables, no database connection.")
            return False
        try:
            cursor = self.connection.cursor()
            
            # Create Customers table
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Customers')
                CREATE TABLE Customers (
                    CustomerID INT PRIMARY KEY IDENTITY(1,1),
                    CustomerName NVARCHAR(100) NOT NULL,
                    Email NVARCHAR(100),
                    Phone NVARCHAR(20),
                    Address NVARCHAR(255),
                    City NVARCHAR(50),
                    Country NVARCHAR(50),
                    CreatedDate DATETIME DEFAULT GETDATE()
                )
            """)
            
            # Create Products table
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Products')
                CREATE TABLE Products (
                    ProductID INT PRIMARY KEY IDENTITY(1,1),
                    ProductName NVARCHAR(100) NOT NULL,
                    Category NVARCHAR(50),
                    UnitPrice DECIMAL(10, 2) NOT NULL,
                    StockQuantity INT DEFAULT 0,
                    Description NVARCHAR(500),
                    CreatedDate DATETIME DEFAULT GETDATE()
                )
            """)
            
            # Create Sales table
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Sales')
                CREATE TABLE Sales (
                    SaleID INT PRIMARY KEY IDENTITY(1,1),
                    CustomerID INT FOREIGN KEY REFERENCES Customers(CustomerID),
                    SaleDate DATETIME DEFAULT GETDATE(),
                    TotalAmount DECIMAL(10, 2) NOT NULL,
                    Status NVARCHAR(20) DEFAULT 'Completed',
                    Notes NVARCHAR(500)
                )
            """)
            
            # Create SaleDetails table
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'SaleDetails')
                CREATE TABLE SaleDetails (
                    SaleDetailID INT PRIMARY KEY IDENTITY(1,1),
                    SaleID INT FOREIGN KEY REFERENCES Sales(SaleID) ON DELETE CASCADE,
                    ProductID INT FOREIGN KEY REFERENCES Products(ProductID),
                    Quantity INT NOT NULL,
                    UnitPrice DECIMAL(10, 2) NOT NULL,
                    Subtotal DECIMAL(10, 2) NOT NULL
                )
            """)
            
            self.connection.commit()
            cursor.close()
            logger.info("Database tables initialized successfully")
            return True
            
        except pyodbc.Error as e:
            logger.error(f"Error initializing tables: {str(e)}")
            self.connection.rollback()
            return False


# Singleton instance
_db_instance = None

def get_database() -> Optional[DatabaseConnection]:
    """
    Get the database connection instance, creating the database and tables if necessary.
    
    Returns:
        An initialized DatabaseConnection object or None if connection fails.
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = DatabaseConnection()
        # First, ensure the database exists.
        if not _db_instance.ensure_database_exists():
            logger.error("Failed to ensure database existence. Aborting.")
            _db_instance = None  # Reset on failure
            return None
        
        # Second, connect to the specific database.
        if not _db_instance.connect():
            logger.error("Failed to connect to the database after ensuring its existence. Aborting.")
            _db_instance = None # Reset on failure
            return None

        # Third, initialize the tables.
        if not _db_instance.initialize_tables():
            logger.error("Failed to initialize database tables. Aborting.")
            _db_instance.disconnect()
            _db_instance = None # Reset on failure
            return None
            
    return _db_instance
