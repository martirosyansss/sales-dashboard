import pyodbc
from typing import Optional, List, Tuple
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Manages SQL Server database connections for Sales Management System"""
    
    def __init__(self, server: str = "localhost", database: str = "SalesManagement", 
                 username: str = "sa", password: str = "Aa123456"):
        """
        Initialize database connection parameters
        
        Args:
            server: SQL Server instance (default: localhost)
            database: Database name (default: SalesManagement)
            username: SQL Server username (default: sa)
            password: SQL Server password (default: Aa123456)
        """
        self.server = server
        self.database = database
        self.username = username
        self.password = password
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
            # Try to create database if it doesn't exist
            return self._create_database_if_not_exists()
    
    def _create_database_if_not_exists(self) -> bool:
        """
        Create database if it doesn't exist
        
        Returns:
            bool: True if database created/connected successfully
        """
        try:
            # Connect to master database to create new database
            connection_string = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={self.server};"
                f"DATABASE=master;"
                f"UID={self.username};"
                f"PWD={self.password};"
                f"TrustServerCertificate=yes;"
            )
            
            master_conn = pyodbc.connect(connection_string)
            master_conn.autocommit = True
            cursor = master_conn.cursor()
            
            # Check if database exists
            cursor.execute(f"SELECT database_id FROM sys.databases WHERE name = '{self.database}'")
            if cursor.fetchone() is None:
                cursor.execute(f"CREATE DATABASE [{self.database}]")
                logger.info(f"Database '{self.database}' created successfully")
            
            cursor.close()
            master_conn.close()
            
            # Now connect to the new database
            return self.connect()
            
        except pyodbc.Error as e:
            logger.error(f"Error creating database: {str(e)}")
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

def get_database() -> DatabaseConnection:
    """Get or create database connection instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = DatabaseConnection()
        if _db_instance.connect():
            _db_instance.initialize_tables()
    return _db_instance
