// Request-driven adapter, shared by both flavours. Target and environment come from Program.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Data.SqlClient;
using System.Globalization;
using System.IO;
using System.Linq;
using ArmSoft.Core;
using ArmSoft.SalesManagement.BusinessObjects;
using ArmSoft.SalesManagement.BusinessObjects.Documents;

internal static partial class NativeCases
{
    private static readonly string DispatchRoot=Path.Combine(Program.Root,"dispatch");
    private static Dictionary<string,object> Map(object value) { return (Dictionary<string,object>)value; }
    private static List<Dictionary<string,object>> Maps(object value)
    { return ((IEnumerable)value).Cast<object>().Select(Map).ToList(); }
    private static string Text(Dictionary<string,object> row,string key) { return Convert.ToString(row[key],CultureInfo.InvariantCulture); }
    private static List<string> Strings(object value)
    { return ((IEnumerable)value).Cast<object>().Select(x=>Convert.ToString(x,CultureInfo.InvariantCulture)).ToList(); }
    private static void Require(bool ok,string code) { if(!ok)throw new DispatchInvalid(code); }
    private sealed class DispatchInvalid:Exception { internal DispatchInvalid(string code):base(code){} }

    internal static int Dispatch()
    {
        BusinessObjectsServer bos=Program.OpenNative();
        try
        {
            Directory.CreateDirectory(DispatchRoot);
            var request=Map(Program.Json.DeserializeObject(Console.In.ReadToEnd()));
            string action=Text(request,"action");
            object result;
            if(action=="options")result=DispatchOptions(bos);
            else if(action=="preview")
            {
                var plan=Map(request["plan"]);var preview=DispatchPreview(bos,plan,null);
                string id=Guid.NewGuid().ToString("D");
                preview["id"]=id;preview["created_utc"]=DateTime.UtcNow.ToString("O");preview["plan"]=plan;
                Save(Path.Combine(DispatchRoot,"preview-"+id+".json"),preview);result=preview;
            }
            else
            {
                string id=new Guid(Text(request,"id")).ToString("D");
                if(action=="execute")result=DispatchExecute(bos,id);
                else if(action=="status")result=DispatchStatus(bos,id);
                else if(action=="reconcile-preview")result=RecoveryPreview(bos,id);
                else if(action=="reconcile-execute")result=RecoveryExecute(bos,id,new Guid(Text(request,"review_id")).ToString("D"),Text(request,"actor"),Text(request,"reason"));
                else throw new DispatchInvalid("bad_request");
            }
            Console.WriteLine(Program.Json.Serialize(new{success=true,result=result}));return 0;
        }
        catch(DispatchInvalid ex)
        {Console.WriteLine(Program.Json.Serialize(new{success=false,code=ex.Message,no_write_dispatched=true}));return 0;}
        finally{bos.Logout();}
    }

    private static object DispatchOptions(BusinessObjectsServer bos)
    {
        var records=new List<Dictionary<string,object>>();
        using(var q=new SqlCommand("SELECT fCODE,fNAME FROM STORAGES WHERE fCLOSE=0 AND fCODE NOT IN ('006','011') ORDER BY fCODE",bos.Connection()))
        using(var r=q.ExecuteReader())while(r.Read())records.Add(new Dictionary<string,object>{{"code",r.GetString(0)},{"name",r.GetString(1)}});
        var warehouses=records.Where(w=>{var s=bos.Cache.Storages.GetStorageByCode(Text(w,"code"));return s.IsAccessible&&!s.IsClosed&&s.Output;}).ToList();
        int gate=WriteGateMaxOrders();
        return new{database=Program.Database,environment=Program.EnvironmentName,warehouses=warehouses,writes_enabled=gate>0,max_orders=gate>0?Math.Min(gate,100):0};
    }

    // WRITES_ENABLED.json = {"max_orders":N}, N>=1. Absent or malformed means writes are disabled.
    private static int WriteGateMaxOrders()
    {
        try
        {
            var gate=Map(Program.Json.DeserializeObject(File.ReadAllText(Path.Combine(Program.Root,"WRITES_ENABLED.json"))));
            object value;
            return gate.TryGetValue("max_orders",out value)&&value is int&&(int)value>=1?(int)value:0;
        }
        catch(Exception){return 0;}
    }

    private static HashSet<Guid> DispatchClaims(string own)
    {
#if ISOLATED_TEST
        HashSet<Guid> claimed=ClaimedOrders();
#else
        var claimed=new HashSet<Guid>();
#endif
        foreach(string file in Directory.GetFiles(DispatchRoot,"operation-*.json"))
        {
            if(Path.GetFileName(file)=="operation-"+own+".json")continue;
            var op=Map(Program.Json.DeserializeObject(File.ReadAllText(file)));
            foreach(string value in Strings(op["claimed_orders"]))claimed.Add(new Guid(value));
        }
        return claimed;
    }

    private static string Version(BusinessObjectsServer bos,Guid id)
    {
        using(var q=new SqlCommand("SELECT fTS FROM DOCUMENTS WHERE fISN=@id AND fDOCTYPE=1",bos.Connection()))
        {q.Parameters.AddWithValue("@id",id);object value=q.ExecuteScalar();Require(value is byte[],"changed");return Convert.ToBase64String((byte[])value);}
    }

    // All persisted order header and line fields, except fields intentionally changed by this chain.
    private static string ContentStamp(BusinessObjectsServer bos,Guid id)
    {
        var tables=new List<object>();
        foreach(string table in new[]{"ORDERS","SALEDOCDETAILS","SALEDOCGIFTS","SALEDOCPRODUCTSONDEPOSIT","SALEDOCEXCHANGES"})
        {
            var records=new List<object>();
            using(var q=new SqlCommand("SELECT * FROM "+table+" WHERE fISN=@id"+(table=="ORDERS"?"":" ORDER BY fROWNUM"),bos.Connection()))
            {
                q.Parameters.AddWithValue("@id",id);
                using(var r=q.ExecuteReader())while(r.Read())
                {
                    var record=new SortedDictionary<string,object>(StringComparer.Ordinal);
                    for(int i=0;i<r.FieldCount;i++)
                    {
                        string name=r.GetName(i);
                        if(table=="ORDERS"&&new[]{"fVANAGENTID","fDELIVERYCAR","fSTATE","fTS"}.Contains(name))continue;
                        object value=r.IsDBNull(i)?null:r.GetValue(i);
                        if(value is byte[])value=Convert.ToBase64String((byte[])value);
                        if(value is DateTime)value=((DateTime)value).ToString("O");
                        record[name]=value;
                    }
                    records.Add(record);
                }
            }
            tables.Add(records);
        }
        return Hash(Program.Json.Serialize(tables));
    }

    private static void DirectoryCheck(BusinessObjectsServer bos,string table,string condition,string code)
    {
        using(var q=new SqlCommand("SELECT COUNT(*) FROM "+table+" WHERE "+condition,bos.Connection()))
        {q.Parameters.AddWithValue("@code",code);Require(Convert.ToInt32(q.ExecuteScalar())==1,"directory");}
    }

    private static List<object> DeliveryManifest(ProductsProviding p)
    {
        return p.DeliveriesList.OrderBy(r=>r.OrderNum).ThenBy(r=>r.Code).ThenBy(r=>r.Operation)
            .Select(r=>(object)new{order=r.OrderNum,product=r.Code,operation=r.Operation.ToString(),quantity=r.Quantity}).ToList();
    }

    private static ProductsProviding ForOrders(BusinessObjectsServer bos,List<SalesOrder> orders,Dictionary<string,object> load)
    {
        Warehouse=Text(load,"warehouse");
        var p=MakeProvision(bos,orders[0],"dispatch",Text(load,"courier"),Text(load,"car"));
        foreach(var order in orders.Skip(1))
            Require(ProductsProviding.AddSalesOrderToDeliveriesList(bos,p,p.DeliveriesList,order,false),"empty");
        p.ReloadProductsGridByDelivery(false,orders[0].PriceListType);
        Require(p.ProductsList.Count>0&&p.ProductsList.All(r=>r.Quantity>0),"empty");return p;
    }

    private static bool ExactSaleQuantities(BusinessObjectsServer bos,Guid sale,Guid providing,string orderNumber)
    {
        var expected=new SortedDictionary<string,decimal>(StringComparer.Ordinal);
        var actual=new SortedDictionary<string,decimal>(StringComparer.Ordinal);
        using(var q=new SqlCommand("SELECT fPRODUCTID,fOPERATION,SUM(fQUANTITY) FROM PROVIDINGDELIVERIES WHERE fISN=@id AND fORDERNUM=@order GROUP BY fPRODUCTID,fOPERATION",bos.Connection()))
        {
            q.Parameters.AddWithValue("@id",providing);q.Parameters.AddWithValue("@order",orderNumber);
            using(var r=q.ExecuteReader())while(r.Read())expected[r.GetInt32(0)+"|"+r.GetString(1)]=r.GetDecimal(2);
        }
        string[] tables={"SALEDOCDETAILS","SALEDOCGIFTS","SALEDOCPRODUCTSONDEPOSIT","SALEDOCEXCHANGES"};
        string[] operations={"RLZ","GFT","DEP","EXC"};
        for(int i=0;i<tables.Length;i++)
        using(var q=new SqlCommand("SELECT fPRODUCTID,SUM(fQUANTITY) FROM "+tables[i]+" WHERE fISN=@id GROUP BY fPRODUCTID",bos.Connection()))
        {
            q.Parameters.AddWithValue("@id",sale);
            using(var r=q.ExecuteReader())while(r.Read())actual[r.GetInt32(0)+"|"+operations[i]]=r.GetDecimal(1);
        }
        return expected.Count>0&&expected.SequenceEqual(actual);
    }

    private static Dictionary<string,object> DispatchPreview(BusinessObjectsServer bos,Dictionary<string,object> plan,string own)
    {
        Day=DateTime.ParseExact(Text(plan,"day"),"yyyy-MM-dd",CultureInfo.InvariantCulture);
        Require(Day>=TimeZoneInfo.ConvertTimeBySystemTimeZoneId(DateTime.UtcNow,"Caucasus Standard Time").Date,"past");
        Require(Convert.ToInt32(plan["revision"])>0,"bad_request");
        var requested=Maps(plan["loads"]);Require(requested.Count>0&&requested.Count<=100,"empty");
        var claimed=DispatchClaims(own);var seen=new HashSet<Guid>();var trips=new HashSet<string>();
        var loads=new List<Dictionary<string,object>>();var skipped=new List<object>();int total=0;
        foreach(var load in requested)
        {
            string trip=Text(load,"trip"),courier=Text(load,"courier"),car=Text(load,"car");Warehouse=Text(load,"warehouse");
            Require(trips.Add(trip)&&trip.Length>0,"bad_request");
            Require(Warehouse!="006"&&Warehouse!="011"&&courier.StartsWith("B",StringComparison.Ordinal),"directory");
            DirectoryCheck(bos,"STORAGES","fCODE=@code AND fCLOSE=0",Warehouse);
            var storage=bos.Cache.Storages.GetStorageByCode(Warehouse);
            Require(storage.IsAccessible&&!storage.IsClosed&&storage.Output,"directory");
            DirectoryCheck(bos,"CARS","fCODE=@code AND fISCLOSED=0",car);
            DirectoryCheck(bos,"SALESAGENTS","fCODE=@code AND fCLOSED=0",courier);
            var orders=new List<SalesOrder>();var sources=new List<Dictionary<string,object>>();
            foreach(string raw in Strings(load["orders"]))
            {
                Guid id=new Guid(raw);Require(seen.Add(id)&&id!=Guid.Empty,"duplicate_order");
                int children;
                using(var q=new SqlCommand("SELECT COUNT(*) FROM DOCPARENTS WHERE fPARENTISN=@id AND fDOCTYPE<>29",bos.Connection()))
                {q.Parameters.AddWithValue("@id",id);children=Convert.ToInt32(q.ExecuteScalar());}
                if(claimed.Contains(id)||children>0)
                {
                    string number="";
                    using(var q=new SqlCommand("SELECT fDOCNUM FROM DOCUMENTS WHERE fISN=@id",bos.Connection()))
                    {q.Parameters.AddWithValue("@id",id);number=Convert.ToString(q.ExecuteScalar());}
                    skipped.Add(new{isn=id.ToString(),number=number,reason=claimed.Contains(id)?"claimed":"has_documents"});continue;
                }
                SalesOrder order=SalesOrder.LoadInstance(bos,id);
                Require(!order.IsDraft&&order.DocDate<=Day,"order_state");
                using(var q=new SqlCommand("SELECT fSTATE FROM ORDERS WHERE fISN=@id",bos.Connection()))
                {q.Parameters.AddWithValue("@id",id);Require(Convert.ToInt32(q.ExecuteScalar())==2,"order_state");}
                Require(!order.ExportedToTaxProgram&&order.ExchangesList.Count==0,"unsupported");
                using(var q=new SqlCommand("SELECT COUNT(*) FROM SALEDOCDETAILS d JOIN PRODUCTS p ON p.fID=d.fPRODUCTID WHERE d.fISN=@id AND p.fCONTAINER=1",bos.Connection()))
                {q.Parameters.AddWithValue("@id",id);Require(Convert.ToInt32(q.ExecuteScalar())==0,"unsupported");}
                sources.Add(new Dictionary<string,object>{{"isn",id.ToString("D")},{"number",order.DocNum},{"sum",order.Sum},
                    {"version",Version(bos,id)},{"content",ContentStamp(bos,id)}});
                order.VanAgentCode=courier;order.DeliveryCar=car;orders.Add(order);total++;
            }
            if(orders.Count==0)continue;
            ProductsProviding p=ForOrders(bos,orders,load);var stocks=new List<object>();var warnings=new List<string>();
            var productNames=new Dictionary<string,string>();
            int agent=bos.Cache.SalesAgents.GetSalesAgentByCode(courier).Id;
            foreach(var row in p.ProductsList)
            {
                decimal available=BatchAvailable(bos,row.Code,orders);Require(available>=row.Quantity,"stock");
                int id=bos.Cache.Products.GetProductByCode(row.Code).Id;decimal agentBefore=Stock(bos,id,agent);
                if(agentBefore<0)warnings.Add("negative_agent_stock: "+row.Code);
                stocks.Add(new{product=row.Code,quantity=row.Quantity,available=available});
                using(var q=new SqlCommand("SELECT fNAME FROM PRODUCTS WHERE fCODE=@code",bos.Connection()))
                {q.Parameters.AddWithValue("@code",row.Code);productNames[row.Code]=Convert.ToString(q.ExecuteScalar());}
            }
            loads.Add(new Dictionary<string,object>{{"trip",trip},{"warehouse",Warehouse},{"courier",courier},{"car",car},
                {"orders",sources},{"manifest",DeliveryManifest(p)},{"stocks",stocks},{"warnings",warnings},{"product_names",productNames}});
        }
        Require(total<=100,"too_many");
        var signature=Hash(Program.Json.Serialize(loads.Select(l=>new{trip=l["trip"],warehouse=l["warehouse"],courier=l["courier"],
            car=l["car"],orders=l["orders"],manifest=l["manifest"]}).ToList()));
        return new Dictionary<string,object>{{"database",Program.Database},{"environment",Program.EnvironmentName},{"day",plan["day"]},
            {"revision",plan["revision"]},{"status","preview"},{"signature",signature},{"loads",loads},{"skipped",skipped},{"order_count",total}};
    }

    private static Dictionary<string,object> DispatchExecute(BusinessObjectsServer bos,string id)
    {
        string path=Path.Combine(DispatchRoot,"operation-"+id+".json");
        if(File.Exists(path))return DispatchStatus(bos,id); // A repeat is always read-only.
        Require(!File.Exists(Path.Combine(Program.Root,"QUARANTINED.json")),"quarantined");
        int maxOrders=WriteGateMaxOrders();Require(maxOrders>0,"writes_disabled");
        string previewPath=Path.Combine(DispatchRoot,"preview-"+id+".json");Require(File.Exists(previewPath),"not_found");
        var preview=Map(Program.Json.DeserializeObject(File.ReadAllText(previewPath)));
        Require(DateTime.UtcNow-DateTime.Parse(Text(preview,"created_utc"),CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind)<TimeSpan.FromMinutes(10),"expired");
        var fresh=DispatchPreview(bos,Map(preview["plan"]),id);
        Require(Text(fresh,"signature")==Text(preview,"signature"),"changed");Require(Convert.ToInt32(fresh["order_count"])>0,"empty");
        Require(Convert.ToInt32(fresh["order_count"])<=maxOrders,"too_many");
        var loads=Maps(fresh["loads"]);var steps=new List<Dictionary<string,object>>();
        var op=new Dictionary<string,object>{{"id",id},{"database",Program.Database},{"day",fresh["day"]},{"status","running"},
            {"claimed_orders",loads.SelectMany(l=>Maps(l["orders"]).Select(o=>Text(o,"isn"))).ToList()},{"loads",loads},{"steps",steps}};
        Save(path,op);bool dispatched=false;int verified=0;
        try
        {
            foreach(var load in loads)
            {
                Warehouse=Text(load,"warehouse");string courier=Text(load,"courier"),car=Text(load,"car");
                var sources=Maps(load["orders"]);var orders=new List<SalesOrder>();
                foreach(var source in sources)
                {
                    Guid orderId=new Guid(Text(source,"isn"));
                    Require(Version(bos,orderId)==Text(source,"version")&&ContentStamp(bos,orderId)==Text(source,"content"),"changed");
                    var order=SalesOrder.LoadInstance(bos,orderId);order.VanAgentCode=courier;order.DeliveryCar=car;
                    var step=new Dictionary<string,object>{{"kind","assignment"},{"order_isn",orderId.ToString()},{"isn",orderId.ToString()},{"status","pending"}};
                    steps.Add(step);Save(path,op);dispatched=true;order.Store(new StoreRequest());
                    order=SalesOrder.LoadInstance(bos,orderId);
                    Require(order.VanAgentCode==courier&&order.DeliveryCar==car&&ContentStamp(bos,orderId)==Text(source,"content"),"verification");
                    source["version"]=Version(bos,orderId);step["status"]="verified";Save(path,op);dispatched=false;verified++;orders.Add(order);
                }
                foreach(var source in sources)Require(Version(bos,new Guid(Text(source,"isn")))==Text(source,"version"),"changed");
                var providing=ForOrders(bos,orders,load);
                Require(Hash(Program.Json.Serialize(DeliveryManifest(providing)))==Hash(Program.Json.Serialize(load["manifest"])),"changed");
                var stockBefore=new Dictionary<int,decimal>();var agentBefore=new Dictionary<int,decimal>();
                int agent=bos.Cache.SalesAgents.GetSalesAgentByCode(courier).Id;
                foreach(var row in providing.ProductsList)
                {
                    Require(BatchAvailable(bos,row.Code,orders)>=row.Quantity,"stock");int pid=bos.Cache.Products.GetProductByCode(row.Code).Id;
                    stockBefore[pid]=Stock(bos,pid,null);agentBefore[pid]=Stock(bos,pid,agent);
                }
                var providingStep=new Dictionary<string,object>{{"kind","providing"},{"isn",providing.ISN.ToString()},{"status","pending"},
                    {"order_isns",orders.Select(o=>o.ISN.ToString()).ToList()}};
                steps.Add(providingStep);Save(path,op);dispatched=true;providing.Store(new StoreRequest{MakeDraft=false});
                TestStopOnce("test-stop-after-providing.once");
                providing=ProductsProviding.LoadInstance(bos,providing.ISN);
                Require(!providing.IsDraft&&providing.Car==car&&providing.SalesAgentCode==courier&&orders.All(o=>providing.Parents.ContainsKey(o.ISN)),"verification");
                foreach(var row in providing.ProductsList)
                {
                    int pid=bos.Cache.Products.GetProductByCode(row.Code).Id;
                    Require(stockBefore[pid]-Stock(bos,pid,null)==row.Quantity&&Stock(bos,pid,agent)-agentBefore[pid]==row.Quantity,"verification");
                }
                providingStep["number"]=providing.DocNum;providingStep["status"]="verified";Save(path,op);dispatched=false;verified++;
                foreach(var source in sources)
                {
                    Guid orderId=new Guid(Text(source,"isn"));Require(ContentStamp(bos,orderId)==Text(source,"content"),"changed");
                    using(var q=new SqlCommand("SELECT COUNT(*) FROM DOCPARENTS WHERE fPARENTISN=@id AND fDOCTYPE<>29 AND fISN<>@providing",bos.Connection()))
                    {q.Parameters.AddWithValue("@id",orderId);q.Parameters.AddWithValue("@providing",providing.ISN);Require(Convert.ToInt32(q.ExecuteScalar())==0,"changed");}
                    var order=SalesOrder.LoadInstance(bos,orderId);var sale=BuildSale(bos,order,providing);
                    var step=new Dictionary<string,object>{{"kind","sale"},{"isn",sale.ISN.ToString()},{"order_isn",orderId.ToString()},
                        {"providing_isn",providing.ISN.ToString()},{"status","pending"}};
                    steps.Add(step);Save(path,op);dispatched=true;sale.Store(new StoreRequest{MakeDraft=false});
                    TestStopOnce("test-stop-after-sale.once");sale=Sale.LoadInstance(bos,sale.ISN);
                    Require(!sale.IsDraft&&sale.Parents.ContainsKey(orderId)&&sale.Sum==Convert.ToDecimal(source["sum"])&&sale.VanAgentCode==courier&&sale.DeliveryCar==car,"verification");
                    Require(ExactSaleQuantities(bos,sale.ISN,providing.ISN,order.DocNum),"verification");
                    using(var q=new SqlCommand("SELECT COALESCE(SUM(CASE WHEN fDBCR='D' THEN fSUM ELSE -fSUM END),0) FROM HICUSTOMERSDEBT WHERE fBASE=@id",bos.Connection()))
                    {q.Parameters.AddWithValue("@id",sale.ISN);Require(Convert.ToDecimal(q.ExecuteScalar())==sale.Sum,"verification");}
                    step["number"]=sale.DocNum;step["sum"]=sale.Sum;step["status"]="verified";Save(path,op);dispatched=false;verified++;
                }
                foreach(var pair in agentBefore)Require(Stock(bos,pair.Key,agent)==pair.Value,"verification");
            }
            op["status"]="complete";Save(path,op);
        }
        catch(Exception ex)
        {
            op["status"]=dispatched?"unknown":verified>0?"partial":"refused";
            op["error_code"]=ex is DispatchInvalid?ex.Message:"native_error";op["error_type"]=ex.GetType().Name;
            Save(path,op); // Claims remain even if the native call was refused.
        }
        return DispatchStatus(bos,id);
    }

    // Proof, made under the run lock and on native's clock, that this operation never wrote and now never can:
    // execute refuses a preview older than 10 minutes, and its first Store changes the order version.
    // A deleted or unreadable journal alone proves nothing, so anything uncertain stays "not_found".
    private static bool ProvablyNeverWrote(BusinessObjectsServer bos,string id,string path)
    {
        try
        {
            try {using(File.Open(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete)){}return false;}
            catch(FileNotFoundException){} // the one result that really means absent
            var preview=Map(Program.Json.DeserializeObject(File.ReadAllText(Path.Combine(DispatchRoot,"preview-"+id+".json"))));
            if(DateTime.UtcNow-DateTime.Parse(Text(preview,"created_utc"),CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind)<=TimeSpan.FromMinutes(10))return false;
            var orders=Maps(preview["loads"]).SelectMany(l=>Maps(l["orders"])).ToList();
            if(orders.Count==0)return false;
            foreach(var order in orders)
            {
                Guid orderId=new Guid(Text(order,"isn"));
                if(Version(bos,orderId)!=Text(order,"version"))return false;
                using(var q=new SqlCommand("SELECT COUNT(*) FROM DOCPARENTS WHERE fPARENTISN=@id AND fDOCTYPE<>29",bos.Connection()))
                {q.Parameters.AddWithValue("@id",orderId);if(Convert.ToInt32(q.ExecuteScalar())!=0)return false;}
            }
            return true;
        }
        catch(Exception){return false;}
    }

    private static Dictionary<string,object> DispatchStatus(BusinessObjectsServer bos,string id)
    {
        string path=Path.Combine(DispatchRoot,"operation-"+id+".json");
        if(!File.Exists(path))throw new DispatchInvalid(ProvablyNeverWrote(bos,id,path)?"no_operation_expired":"not_found");
        var op=Map(Program.Json.DeserializeObject(File.ReadAllText(path)));
        bool drift=false;
        var sources=Maps(op["loads"]).SelectMany(l=>Maps(l["orders"])).ToDictionary(s=>new Guid(Text(s,"isn")));
        foreach(var load in Maps(op["loads"]))foreach(var source in Maps(load["orders"]))
        {
            Guid sourceId=new Guid(Text(source,"isn"));
            if(ContentStamp(bos,sourceId)!=Text(source,"content"))drift=true;
            if(Text(op,"status")=="complete")
            {
                var order=SalesOrder.LoadInstance(bos,sourceId);
                if(order.VanAgentCode!=Text(load,"courier")||order.DeliveryCar!=Text(load,"car"))drift=true;
            }
        }
        foreach(var step in Maps(op["steps"]))
        {
            Guid isn=new Guid(Text(step,"isn"));
            if(Text(step,"status")=="confirmed_absent")
            {bool exists=Exists(bos,isn);step["observed_exists"]=exists;if(exists)drift=true;continue;}
            using(var q=new SqlCommand("SELECT fDOCSTATE,fDOCNUM FROM DOCUMENTS WHERE fISN=@id",bos.Connection()))
            {
                q.Parameters.AddWithValue("@id",isn);
                using(var r=q.ExecuteReader())if(r.Read()){step["observed_exists"]=true;step["observed_registered"]=r.GetByte(0)==2;step["observed_number"]=r.GetString(1);if(r.GetByte(0)!=2)drift=true;}
                else {step["observed_exists"]=false;drift=true;}
            }
            if(Text(step,"kind")!="assignment"&&step.ContainsKey("observed_registered")&&(bool)step["observed_registered"])
            {
                List<string> parents=step.ContainsKey("order_isns")?Strings(step["order_isns"]):new List<string>{Text(step,"order_isn")};
                foreach(string parent in parents)
                using(var q=new SqlCommand("SELECT COUNT(*) FROM DOCPARENTS WHERE fISN=@id AND fPARENTISN=@parent AND fPARENTDOCTYPE=1",bos.Connection()))
                {q.Parameters.AddWithValue("@id",isn);q.Parameters.AddWithValue("@parent",new Guid(parent));if(Convert.ToInt32(q.ExecuteScalar())!=1)drift=true;}
                if(Text(step,"kind")=="sale")
                {
                    Guid orderId=new Guid(Text(step,"order_isn"));var source=sources[orderId];
                    var sale=Sale.LoadInstance(bos,isn);
                    var load=Maps(op["loads"]).Single(l=>Maps(l["orders"]).Any(s=>new Guid(Text(s,"isn"))==orderId));
                    if(sale.Sum!=Convert.ToDecimal(source["sum"])||sale.VanAgentCode!=Text(load,"courier")||sale.DeliveryCar!=Text(load,"car")
                        ||!ExactSaleQuantities(bos,isn,new Guid(Text(step,"providing_isn")),Text(source,"number")))drift=true;
                }
                else if(Text(step,"kind")=="providing")
                {
                    var providing=ProductsProviding.LoadInstance(bos,isn);
                    var load=Maps(op["loads"]).Single(l=>Maps(l["orders"]).Any(s=>new Guid(Text(s,"isn"))==new Guid(parents[0])));
                    if(providing.Storage!=Text(load,"warehouse")||providing.Car!=Text(load,"car")||providing.SalesAgentCode!=Text(load,"courier")
                        ||Hash(Program.Json.Serialize(DeliveryManifest(providing)))!=Hash(Program.Json.Serialize(load["manifest"])))drift=true;
                }
            }
        }
        if(Text(op,"status")=="running"||Text(op,"status")=="complete"&&drift)
        {op["journal_status"]=op["status"];op["status"]="unknown";}
        op["drift_detected"]=drift;op["replay_dispatched"]=false;op["environment"]=Program.EnvironmentName;return op;
    }
}
