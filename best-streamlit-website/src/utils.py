def load_data(file_path):
    import pandas as pd
    return pd.read_csv(file_path)

def save_data(data, file_path):
    import pandas as pd
    data.to_csv(file_path, index=False)

def format_date(date):
    return date.strftime("%Y-%m-%d")

def generate_unique_id():
    import uuid
    return str(uuid.uuid4())