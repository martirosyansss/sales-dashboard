import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from typing import List, Optional
from database import get_database, DatabaseConnection


class SalesManagementApp:
    """Main Sales Management Application"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Sales Management System")
        self.root.geometry("1200x700")
        
        # Get database connection
        self.db = get_database()
        
        # Create main interface
        self.create_menu()
        self.create_notebook()
        
    def create_menu(self):
        """Create menu bar"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Refresh All", command=self.refresh_all)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)
    
    def create_notebook(self):
        """Create tabbed interface"""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Create tabs
        self.create_customers_tab()
        self.create_products_tab()
        self.create_sales_tab()
        self.create_reports_tab()
    
    def create_customers_tab(self):
        """Create Customers management tab"""
        customers_frame = ttk.Frame(self.notebook)
        self.notebook.add(customers_frame, text="Customers")
        
        # Top frame for form
        form_frame = ttk.LabelFrame(customers_frame, text="Customer Information", padding=10)
        form_frame.pack(fill='x', padx=10, pady=5)
        
        # Customer form fields
        ttk.Label(form_frame, text="Name:").grid(row=0, column=0, sticky='w', pady=2)
        self.customer_name = ttk.Entry(form_frame, width=30)
        self.customer_name.grid(row=0, column=1, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Email:").grid(row=0, column=2, sticky='w', pady=2)
        self.customer_email = ttk.Entry(form_frame, width=30)
        self.customer_email.grid(row=0, column=3, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Phone:").grid(row=1, column=0, sticky='w', pady=2)
        self.customer_phone = ttk.Entry(form_frame, width=30)
        self.customer_phone.grid(row=1, column=1, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Address:").grid(row=1, column=2, sticky='w', pady=2)
        self.customer_address = ttk.Entry(form_frame, width=30)
        self.customer_address.grid(row=1, column=3, padx=5, pady=2)
        
        ttk.Label(form_frame, text="City:").grid(row=2, column=0, sticky='w', pady=2)
        self.customer_city = ttk.Entry(form_frame, width=30)
        self.customer_city.grid(row=2, column=1, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Country:").grid(row=2, column=2, sticky='w', pady=2)
        self.customer_country = ttk.Entry(form_frame, width=30)
        self.customer_country.grid(row=2, column=3, padx=5, pady=2)
        
        # Buttons
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=3, column=0, columnspan=4, pady=10)
        
        ttk.Button(btn_frame, text="Add Customer", command=self.add_customer).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Update Customer", command=self.update_customer).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Delete Customer", command=self.delete_customer).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Clear Form", command=self.clear_customer_form).pack(side='left', padx=5)
        
        # Treeview for customers list
        tree_frame = ttk.Frame(customers_frame)
        tree_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal")
        
        self.customers_tree = ttk.Treeview(tree_frame, columns=("ID", "Name", "Email", "Phone", "City", "Country"),
                                           show='headings', yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        vsb.config(command=self.customers_tree.yview)
        hsb.config(command=self.customers_tree.xview)
        
        # Define columns
        self.customers_tree.heading("ID", text="ID")
        self.customers_tree.heading("Name", text="Name")
        self.customers_tree.heading("Email", text="Email")
        self.customers_tree.heading("Phone", text="Phone")
        self.customers_tree.heading("City", text="City")
        self.customers_tree.heading("Country", text="Country")
        
        self.customers_tree.column("ID", width=50)
        self.customers_tree.column("Name", width=150)
        self.customers_tree.column("Email", width=150)
        self.customers_tree.column("Phone", width=100)
        self.customers_tree.column("City", width=100)
        self.customers_tree.column("Country", width=100)
        
        # Pack treeview and scrollbars
        self.customers_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        # Bind selection event
        self.customers_tree.bind('<<TreeviewSelect>>', self.on_customer_select)
        
        # Load customers
        self.load_customers()
    
    def create_products_tab(self):
        """Create Products management tab"""
        products_frame = ttk.Frame(self.notebook)
        self.notebook.add(products_frame, text="Products")
        
        # Top frame for form
        form_frame = ttk.LabelFrame(products_frame, text="Product Information", padding=10)
        form_frame.pack(fill='x', padx=10, pady=5)
        
        # Product form fields
        ttk.Label(form_frame, text="Product Name:").grid(row=0, column=0, sticky='w', pady=2)
        self.product_name = ttk.Entry(form_frame, width=30)
        self.product_name.grid(row=0, column=1, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Category:").grid(row=0, column=2, sticky='w', pady=2)
        self.product_category = ttk.Entry(form_frame, width=30)
        self.product_category.grid(row=0, column=3, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Unit Price:").grid(row=1, column=0, sticky='w', pady=2)
        self.product_price = ttk.Entry(form_frame, width=30)
        self.product_price.grid(row=1, column=1, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Stock Quantity:").grid(row=1, column=2, sticky='w', pady=2)
        self.product_stock = ttk.Entry(form_frame, width=30)
        self.product_stock.grid(row=1, column=3, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Description:").grid(row=2, column=0, sticky='w', pady=2)
        self.product_description = ttk.Entry(form_frame, width=70)
        self.product_description.grid(row=2, column=1, columnspan=3, padx=5, pady=2)
        
        # Buttons
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=3, column=0, columnspan=4, pady=10)
        
        ttk.Button(btn_frame, text="Add Product", command=self.add_product).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Update Product", command=self.update_product).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Delete Product", command=self.delete_product).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Clear Form", command=self.clear_product_form).pack(side='left', padx=5)
        
        # Treeview for products list
        tree_frame = ttk.Frame(products_frame)
        tree_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal")
        
        self.products_tree = ttk.Treeview(tree_frame, columns=("ID", "Name", "Category", "Price", "Stock"),
                                          show='headings', yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        vsb.config(command=self.products_tree.yview)
        hsb.config(command=self.products_tree.xview)
        
        self.products_tree.heading("ID", text="ID")
        self.products_tree.heading("Name", text="Product Name")
        self.products_tree.heading("Category", text="Category")
        self.products_tree.heading("Price", text="Unit Price")
        self.products_tree.heading("Stock", text="Stock")
        
        self.products_tree.column("ID", width=50)
        self.products_tree.column("Name", width=200)
        self.products_tree.column("Category", width=150)
        self.products_tree.column("Price", width=100)
        self.products_tree.column("Stock", width=100)
        
        self.products_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        self.products_tree.bind('<<TreeviewSelect>>', self.on_product_select)
        
        self.load_products()
    
    def create_sales_tab(self):
        """Create Sales management tab"""
        sales_frame = ttk.Frame(self.notebook)
        self.notebook.add(sales_frame, text="Sales")
        
        # Top frame for new sale
        form_frame = ttk.LabelFrame(sales_frame, text="New Sale", padding=10)
        form_frame.pack(fill='x', padx=10, pady=5)
        
        ttk.Label(form_frame, text="Customer:").grid(row=0, column=0, sticky='w', pady=2)
        self.sale_customer = ttk.Combobox(form_frame, width=30, state='readonly')
        self.sale_customer.grid(row=0, column=1, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Product:").grid(row=0, column=2, sticky='w', pady=2)
        self.sale_product = ttk.Combobox(form_frame, width=30, state='readonly')
        self.sale_product.grid(row=0, column=3, padx=5, pady=2)
        
        ttk.Label(form_frame, text="Quantity:").grid(row=1, column=0, sticky='w', pady=2)
        self.sale_quantity = ttk.Entry(form_frame, width=30)
        self.sale_quantity.grid(row=1, column=1, padx=5, pady=2)
        
        ttk.Button(form_frame, text="Create Sale", command=self.create_sale).grid(row=1, column=3, padx=5, pady=10)
        
        # Treeview for sales list
        tree_frame = ttk.Frame(sales_frame)
        tree_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        
        self.sales_tree = ttk.Treeview(tree_frame, columns=("ID", "Customer", "Date", "Amount", "Status"),
                                       show='headings', yscrollcommand=vsb.set)
        
        vsb.config(command=self.sales_tree.yview)
        
        self.sales_tree.heading("ID", text="Sale ID")
        self.sales_tree.heading("Customer", text="Customer")
        self.sales_tree.heading("Date", text="Date")
        self.sales_tree.heading("Amount", text="Total Amount")
        self.sales_tree.heading("Status", text="Status")
        
        self.sales_tree.column("ID", width=80)
        self.sales_tree.column("Customer", width=200)
        self.sales_tree.column("Date", width=150)
        self.sales_tree.column("Amount", width=120)
        self.sales_tree.column("Status", width=100)
        
        self.sales_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        self.load_sales()
        self.load_sale_dropdowns()
    
    def create_reports_tab(self):
        """Create Reports tab"""
        reports_frame = ttk.Frame(self.notebook)
        self.notebook.add(reports_frame, text="Reports")
        
        # Summary frame
        summary_frame = ttk.LabelFrame(reports_frame, text="Sales Summary", padding=20)
        summary_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.total_sales_label = ttk.Label(summary_frame, text="Total Sales: $0.00", font=('Arial', 14, 'bold'))
        self.total_sales_label.pack(pady=10)
        
        self.total_customers_label = ttk.Label(summary_frame, text="Total Customers: 0", font=('Arial', 12))
        self.total_customers_label.pack(pady=5)
        
        self.total_products_label = ttk.Label(summary_frame, text="Total Products: 0", font=('Arial', 12))
        self.total_products_label.pack(pady=5)
        
        ttk.Button(summary_frame, text="Refresh Reports", command=self.load_reports).pack(pady=20)
        
        self.load_reports()
    
    # Customer operations
    def load_customers(self):
        """Load all customers from database"""
        for item in self.customers_tree.get_children():
            self.customers_tree.delete(item)
        
        query = "SELECT CustomerID, CustomerName, Email, Phone, City, Country FROM Customers ORDER BY CustomerName"
        results = self.db.execute_query(query)
        
        if results:
            for row in results:
                self.customers_tree.insert('', 'end', values=row)
    
    def add_customer(self):
        """Add new customer to database"""
        name = self.customer_name.get().strip()
        email = self.customer_email.get().strip()
        phone = self.customer_phone.get().strip()
        address = self.customer_address.get().strip()
        city = self.customer_city.get().strip()
        country = self.customer_country.get().strip()
        
        if not name:
            messagebox.showerror("Error", "Customer name is required!")
            return
        
        query = """INSERT INTO Customers (CustomerName, Email, Phone, Address, City, Country) 
                   VALUES (?, ?, ?, ?, ?, ?)"""
        params = (name, email, phone, address, city, country)
        
        if self.db.execute_non_query(query, params):
            messagebox.showinfo("Success", "Customer added successfully!")
            self.clear_customer_form()
            self.load_customers()
            self.load_sale_dropdowns()
        else:
            messagebox.showerror("Error", "Failed to add customer!")
    
    def update_customer(self):
        """Update selected customer"""
        selected = self.customers_tree.selection()
        if not selected:
            messagebox.showerror("Error", "Please select a customer to update!")
            return
        
        customer_id = self.customers_tree.item(selected[0])['values'][0]
        name = self.customer_name.get().strip()
        
        if not name:
            messagebox.showerror("Error", "Customer name is required!")
            return
        
        query = """UPDATE Customers SET CustomerName=?, Email=?, Phone=?, Address=?, City=?, Country=? 
                   WHERE CustomerID=?"""
        params = (name, self.customer_email.get().strip(), self.customer_phone.get().strip(),
                  self.customer_address.get().strip(), self.customer_city.get().strip(),
                  self.customer_country.get().strip(), customer_id)
        
        if self.db.execute_non_query(query, params):
            messagebox.showinfo("Success", "Customer updated successfully!")
            self.clear_customer_form()
            self.load_customers()
            self.load_sale_dropdowns()
        else:
            messagebox.showerror("Error", "Failed to update customer!")
    
    def delete_customer(self):
        """Delete selected customer"""
        selected = self.customers_tree.selection()
        if not selected:
            messagebox.showerror("Error", "Please select a customer to delete!")
            return
        
        if messagebox.askyesno("Confirm", "Are you sure you want to delete this customer?"):
            customer_id = self.customers_tree.item(selected[0])['values'][0]
            query = "DELETE FROM Customers WHERE CustomerID=?"
            
            if self.db.execute_non_query(query, (customer_id,)):
                messagebox.showinfo("Success", "Customer deleted successfully!")
                self.clear_customer_form()
                self.load_customers()
                self.load_sale_dropdowns()
            else:
                messagebox.showerror("Error", "Failed to delete customer!")
    
    def on_customer_select(self, event):
        """Handle customer selection"""
        selected = self.customers_tree.selection()
        if selected:
            values = self.customers_tree.item(selected[0])['values']
            self.customer_name.delete(0, tk.END)
            self.customer_name.insert(0, values[1])
            self.customer_email.delete(0, tk.END)
            self.customer_email.insert(0, values[2] if values[2] else "")
            self.customer_phone.delete(0, tk.END)
            self.customer_phone.insert(0, values[3] if values[3] else "")
            self.customer_city.delete(0, tk.END)
            self.customer_city.insert(0, values[4] if values[4] else "")
            self.customer_country.delete(0, tk.END)
            self.customer_country.insert(0, values[5] if values[5] else "")
    
    def clear_customer_form(self):
        """Clear customer form fields"""
        self.customer_name.delete(0, tk.END)
        self.customer_email.delete(0, tk.END)
        self.customer_phone.delete(0, tk.END)
        self.customer_address.delete(0, tk.END)
        self.customer_city.delete(0, tk.END)
        self.customer_country.delete(0, tk.END)
    
    # Product operations
    def load_products(self):
        """Load all products from database"""
        for item in self.products_tree.get_children():
            self.products_tree.delete(item)
        
        query = "SELECT ProductID, ProductName, Category, UnitPrice, StockQuantity FROM Products ORDER BY ProductName"
        results = self.db.execute_query(query)
        
        if results:
            for row in results:
                self.products_tree.insert('', 'end', values=row)
    
    def add_product(self):
        """Add new product to database"""
        name = self.product_name.get().strip()
        category = self.product_category.get().strip()
        
        try:
            price = float(self.product_price.get().strip())
            stock = int(self.product_stock.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Invalid price or stock quantity!")
            return
        
        if not name:
            messagebox.showerror("Error", "Product name is required!")
            return
        
        query = """INSERT INTO Products (ProductName, Category, UnitPrice, StockQuantity, Description) 
                   VALUES (?, ?, ?, ?, ?)"""
        params = (name, category, price, stock, self.product_description.get().strip())
        
        if self.db.execute_non_query(query, params):
            messagebox.showinfo("Success", "Product added successfully!")
            self.clear_product_form()
            self.load_products()
            self.load_sale_dropdowns()
        else:
            messagebox.showerror("Error", "Failed to add product!")
    
    def update_product(self):
        """Update selected product"""
        selected = self.products_tree.selection()
        if not selected:
            messagebox.showerror("Error", "Please select a product to update!")
            return
        
        product_id = self.products_tree.item(selected[0])['values'][0]
        name = self.product_name.get().strip()
        
        try:
            price = float(self.product_price.get().strip())
            stock = int(self.product_stock.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Invalid price or stock quantity!")
            return
        
        if not name:
            messagebox.showerror("Error", "Product name is required!")
            return
        
        query = """UPDATE Products SET ProductName=?, Category=?, UnitPrice=?, StockQuantity=?, Description=? 
                   WHERE ProductID=?"""
        params = (name, self.product_category.get().strip(), price, stock,
                  self.product_description.get().strip(), product_id)
        
        if self.db.execute_non_query(query, params):
            messagebox.showinfo("Success", "Product updated successfully!")
            self.clear_product_form()
            self.load_products()
            self.load_sale_dropdowns()
        else:
            messagebox.showerror("Error", "Failed to update product!")
    
    def delete_product(self):
        """Delete selected product"""
        selected = self.products_tree.selection()
        if not selected:
            messagebox.showerror("Error", "Please select a product to delete!")
            return
        
        if messagebox.askyesno("Confirm", "Are you sure you want to delete this product?"):
            product_id = self.products_tree.item(selected[0])['values'][0]
            query = "DELETE FROM Products WHERE ProductID=?"
            
            if self.db.execute_non_query(query, (product_id,)):
                messagebox.showinfo("Success", "Product deleted successfully!")
                self.clear_product_form()
                self.load_products()
                self.load_sale_dropdowns()
            else:
                messagebox.showerror("Error", "Failed to delete product!")
    
    def on_product_select(self, event):
        """Handle product selection"""
        selected = self.products_tree.selection()
        if selected:
            values = self.products_tree.item(selected[0])['values']
            self.product_name.delete(0, tk.END)
            self.product_name.insert(0, values[1])
            self.product_category.delete(0, tk.END)
            self.product_category.insert(0, values[2] if values[2] else "")
            self.product_price.delete(0, tk.END)
            self.product_price.insert(0, values[3])
            self.product_stock.delete(0, tk.END)
            self.product_stock.insert(0, values[4])
    
    def clear_product_form(self):
        """Clear product form fields"""
        self.product_name.delete(0, tk.END)
        self.product_category.delete(0, tk.END)
        self.product_price.delete(0, tk.END)
        self.product_stock.delete(0, tk.END)
        self.product_description.delete(0, tk.END)
    
    # Sales operations
    def load_sale_dropdowns(self):
        """Load customers and products into dropdowns"""
        # Load customers
        query = "SELECT CustomerID, CustomerName FROM Customers ORDER BY CustomerName"
        customers = self.db.execute_query(query)
        if customers:
            customer_list = [f"{row[0]} - {row[1]}" for row in customers]
            self.sale_customer['values'] = customer_list
        
        # Load products
        query = "SELECT ProductID, ProductName, UnitPrice FROM Products WHERE StockQuantity > 0 ORDER BY ProductName"
        products = self.db.execute_query(query)
        if products:
            product_list = [f"{row[0]} - {row[1]} (${row[2]})" for row in products]
            self.sale_product['values'] = product_list
    
    def load_sales(self):
        """Load all sales from database"""
        for item in self.sales_tree.get_children():
            self.sales_tree.delete(item)
        
        query = """SELECT s.SaleID, c.CustomerName, s.SaleDate, s.TotalAmount, s.Status 
                   FROM Sales s 
                   JOIN Customers c ON s.CustomerID = c.CustomerID 
                   ORDER BY s.SaleDate DESC"""
        results = self.db.execute_query(query)
        
        if results:
            for row in results:
                # Format date and amount
                sale_date = row[2].strftime("%Y-%m-%d %H:%M") if row[2] else ""
                formatted_row = (row[0], row[1], sale_date, f"${row[3]:.2f}", row[4])
                self.sales_tree.insert('', 'end', values=formatted_row)
    
    def create_sale(self):
        """Create a new sale"""
        customer_str = self.sale_customer.get()
        product_str = self.sale_product.get()
        
        if not customer_str or not product_str:
            messagebox.showerror("Error", "Please select customer and product!")
            return
        
        try:
            quantity = int(self.sale_quantity.get().strip())
            if quantity <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Invalid quantity!")
            return
        
        # Extract IDs
        customer_id = int(customer_str.split(' - ')[0])
        product_id = int(product_str.split(' - ')[0])
        
        # Get product details
        query = "SELECT UnitPrice, StockQuantity FROM Products WHERE ProductID=?"
        result = self.db.execute_query(query, (product_id,))
        
        if not result:
            messagebox.showerror("Error", "Product not found!")
            return
        
        unit_price, stock = result[0]
        
        if quantity > stock:
            messagebox.showerror("Error", f"Insufficient stock! Available: {stock}")
            return
        
        subtotal = unit_price * quantity
        
        # Create sale
        query = "INSERT INTO Sales (CustomerID, TotalAmount, Status) VALUES (?, ?, 'Completed')"
        if self.db.execute_non_query(query, (customer_id, subtotal)):
            # Get the sale ID
            sale_id_query = "SELECT MAX(SaleID) FROM Sales"
            sale_id_result = self.db.execute_query(sale_id_query)
            sale_id = sale_id_result[0][0]
            
            # Add sale detail
            detail_query = """INSERT INTO SaleDetails (SaleID, ProductID, Quantity, UnitPrice, Subtotal) 
                             VALUES (?, ?, ?, ?, ?)"""
            if self.db.execute_non_query(detail_query, (sale_id, product_id, quantity, unit_price, subtotal)):
                # Update stock
                update_stock = "UPDATE Products SET StockQuantity = StockQuantity - ? WHERE ProductID=?"
                self.db.execute_non_query(update_stock, (quantity, product_id))
                
                messagebox.showinfo("Success", f"Sale created successfully! Total: ${subtotal:.2f}")
                self.sale_quantity.delete(0, tk.END)
                self.load_sales()
                self.load_products()
                self.load_sale_dropdowns()
                self.load_reports()
            else:
                messagebox.showerror("Error", "Failed to create sale details!")
        else:
            messagebox.showerror("Error", "Failed to create sale!")
    
    def load_reports(self):
        """Load sales reports"""
        # Total sales
        query = "SELECT ISNULL(SUM(TotalAmount), 0) FROM Sales"
        result = self.db.execute_query(query)
        total_sales = result[0][0] if result else 0
        self.total_sales_label.config(text=f"Total Sales: ${total_sales:.2f}")
        
        # Total customers
        query = "SELECT COUNT(*) FROM Customers"
        result = self.db.execute_query(query)
        total_customers = result[0][0] if result else 0
        self.total_customers_label.config(text=f"Total Customers: {total_customers}")
        
        # Total products
        query = "SELECT COUNT(*) FROM Products"
        result = self.db.execute_query(query)
        total_products = result[0][0] if result else 0
        self.total_products_label.config(text=f"Total Products: {total_products}")
    
    def refresh_all(self):
        """Refresh all data"""
        self.load_customers()
        self.load_products()
        self.load_sales()
        self.load_sale_dropdowns()
        self.load_reports()
        messagebox.showinfo("Success", "All data refreshed!")
    
    def show_about(self):
        """Show about dialog"""
        messagebox.showinfo("About", "Sales Management System\nVersion 1.0\n\nManage customers, products, and sales.")


def main():
    """Main application entry point"""
    root = tk.Tk()
    app = SalesManagementApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
