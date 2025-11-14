# Sales Management System

A comprehensive sales management application built with Python and SQL Server.

## Features

- **Customer Management**: Add, update, delete, and view customers
- **Product Management**: Manage product inventory with pricing and stock tracking
- **Sales Processing**: Create sales transactions with automatic stock updates
- **Reports**: View sales summaries and statistics

## Prerequisites

1. **SQL Server**: Make sure SQL Server is installed and running
2. **ODBC Driver**: Install ODBC Driver 17 for SQL Server
   - Download from: https://docs.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server

## Database Configuration

The application connects to SQL Server with the following default settings:
- Server: `localhost`
- Database: `SalesManagement` (created automatically)
- Username: `sa`
- Password: `Aa123456`

## Installation

1. Install Python dependencies:
```powershell
pip install -r requirements.txt
```

2. Run the application:
```powershell
python sales_management.py
```

## Usage

### Customers Tab
- Add new customers with contact information
- Edit existing customer details
- Delete customers
- View all customers in a searchable list

### Products Tab
- Add products with pricing and stock levels
- Update product information
- Delete products
- Monitor inventory

### Sales Tab
- Create new sales by selecting customer and product
- Specify quantity
- Automatic stock deduction
- View sales history

### Reports Tab
- Total sales revenue
- Customer count
- Product count

## Database Structure

The application automatically creates the following tables:

- **Customers**: Customer information
- **Products**: Product catalog with inventory
- **Sales**: Sales transactions
- **SaleDetails**: Line items for each sale

## Troubleshooting

### Connection Issues
- Verify SQL Server is running
- Check if sa account is enabled
- Ensure TCP/IP is enabled in SQL Server Configuration Manager
- Verify firewall settings allow SQL Server connections

### ODBC Driver Not Found
Install ODBC Driver 17 for SQL Server from Microsoft's website.

## License

This project is provided as-is for educational and commercial use.
