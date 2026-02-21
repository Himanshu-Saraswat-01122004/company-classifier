"""
create_sample_input.py - Generate a sample companies_input.xlsx for testing.

Run:
    python create_sample_input.py
"""

import pandas as pd

SAMPLE_COMPANIES = [
    # S.NO, CIN, Company Name
    (1, "U72200TN2000PLC123456", "Zoho Corporation Pvt Ltd"),
    (2, "U32109MH2004PLC789012", "Tata Consultancy Services Ltd"),
    (3, "U74999DL2007PTC234567", "Qualcomm India Pvt Ltd"),
    (4, "U40300KA1994PLC890123", "Infosys Ltd"),
    (5, "U29100AP2010PLC345678", "Bharat Electronics Limited"),
    (6, "U72900MH2001PTC456789", "Wipro Ltd"),
    (7, "U67120DL2012OPC567890", "Goldman Sachs Services Pvt Ltd"),
    (8, "U31400WB1992PLC678901", "ABB India Ltd"),
    (9, "U72200KA1998PLC789012", "Flipkart Internet Pvt Ltd"),
    (10, "U31300MH2000PLC890123", "Texas Instruments India Pvt Ltd"),
    (11, "U72200TN2011PTC901234", "Freshworks Technologies Pvt Ltd"),
    (12, "U36992DL2000PLC012345", "HCL Technologies Ltd"),
    (13, "U40100KA2000PLC123456", "Intel Technology India Pvt Ltd"),
    (14, "U30007MH2004PLC234567", "Siemens Ltd"),
    (15, "U72200GJ2009PTC345678", "Jio Platforms Ltd"),
    (16, "U74999MH2018PTC456789", "BrowserStack Software Pvt Ltd"),
    (17, "U31200TN2003PLC567890", "STMicroelectronics Pvt Ltd"),
    (18, "U40300KA2002PLC678901", "Cisco Systems India Pvt Ltd"),
    (19, "U93090MH1999PLC789012", "Paytm (One97 Communications) Ltd"),
    (20, "U29220DL1986PLC890123", "Hindustan Aeronautics Limited"),
    (21, "U51909MH2011PTC901234", "Meesho Pvt Ltd"),
    (22, "U40200DL1995PLC012345", "NTPC Ltd"),
    (23, "U72200KA2012PTC123456", "Swiggy (Bundl Technologies) Pvt Ltd"),
    (24, "U93090MH2007PLC234567", "Nykaa (FSN E-Commerce Ventures) Ltd"),
    (25, "U31400KA2004PLC345678", "Bosch Ltd"),
    (26, "U72200TN2001PLC456789", "Ola Cabs (ANI Technologies) Pvt Ltd"),
    (27, "U74900DL2000PTC567890", "Samsung India Electronics Pvt Ltd"),
    (28, "U64200MH2014PTC678901", "Razorpay Software Pvt Ltd"),
    (29, "U26920MH2000PLC789012", "Tata Steel Ltd"),
    (30, "U72200KA2015PTC890123", "NVIDIA Graphics Pvt Ltd"),
]

df = pd.DataFrame(SAMPLE_COMPANIES, columns=["S.NO", "CIN", "Company Name"])
output_path = "companies_input.xlsx"
df.to_excel(output_path, index=False, engine="openpyxl")
print(f"Sample input file created: {output_path}  ({len(df)} companies)")
