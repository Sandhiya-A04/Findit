
FINDIT - SMART LOST & FOUND PORTAL
=================================

PROJECT DESCRIPTION
A web application for reporting lost and found items.

FEATURES
1. Report Lost Items
2. Report Found Items
3. Search Items
4. Filter Items
5. Upload Item Photos
6. Smart Matching Suggestions
7. Mark Items as Returned
8. Dashboard Statistics

TECHNOLOGIES
Python
Flask
SQLite
HTML
CSS
Bootstrap concepts

INSTALLATION

Step 1:
Create a virtual environment.

python -m venv venv

Step 2:
Activate the environment.

venv\Scripts\activate

Step 3:
Install dependencies.

pip install -r requirements.txt

Step 4:
Run the application.

python app.py

Step 5:
Open browser.

http://127.0.0.1:5000

DATABASE
SQLite database is automatically created
when the application starts.

DATABASE FILE
lost_found.db

IMAGE STORAGE
Uploaded images are saved in the uploads folder.

CLOUD COMPUTING CONCEPT

Future AWS deployment:

Amazon EC2 - Application hosting
Amazon S3 - Image storage
Amazon DynamoDB - Database
AWS IAM - Access management

CURRENT VERSION
Runs locally without AWS cloud-service charges.
