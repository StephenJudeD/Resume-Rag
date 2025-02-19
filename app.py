import dash
from dash import html, dcc, Input, Output, State
import os
import tempfile
from openai import OpenAI
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from google.cloud import storage
import logging
import os

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Write temporary credentials file from env var
if os.getenv("GOOGLE_CREDENTIALS_JSON"):
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp:
        temp.write(os.environ["GOOGLE_CREDENTIALS_JSON"])
        temp.flush()
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp.name
        logger.debug(f"Temporary credentials file created at {temp.name}")
else:
    logger.error("GOOGLE_CREDENTIALS_JSON environment variable not set.")




def download_index_folder(bucket_name, source_folder, destination_dir):
    """Download all files in a GCS folder to a local directory"""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    
    # Create destination directory if it doesn't exist
    os.makedirs(destination_dir, exist_ok=True)
    
    # List all blobs in the folder
    blobs = bucket.list_blobs(prefix=source_folder)
    
    for blob in blobs:
        # Skip directories (they have trailing '/')
        if blob.name.endswith('/'):
            continue
            
        # Create local file path
        local_path = os.path.join(destination_dir, os.path.basename(blob.name))
        
        # Download the file
        blob.download_to_filename(local_path)
        logger.debug(f"Downloaded {blob.name} to {local_path}")

def load_vector_store(embeddings):
    bucket_name = os.getenv("GCS_BUCKET_NAME", "ragsd-resume-bucket")
    index_path = os.getenv("GCS_INDEX_PATH", "faiss_indexes/cv_index_text-embedding-3-large")
    destination_folder = "/tmp/faiss_index"
    
    # Download all files in the index folder
    download_index_folder(bucket_name, index_path, destination_folder)
    
    # Debug directory contents
    contents = os.listdir(destination_folder)
    logger.debug(f"Index files downloaded: {contents}")
    
    # Load FAISS from the directory
    try:
        vector_store = FAISS.load_local(
            destination_folder,
            embeddings,
            allow_dangerous_deserialization=True
        )
        return vector_store
    except Exception as error:
        logger.error("Error loading index. Verify downloaded files match FAISS requirements.")
        raise error

class CVQueryApp:
    def __init__(self):
        try:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not found!")
    
            self.client = OpenAI(api_key=api_key)
    
            self.embeddings = OpenAIEmbeddings(
                model="text-embedding-3-large",
                openai_api_key=api_key
            )
    
            self.vector_store = load_vector_store(self.embeddings)
    
        except Exception as e:
            logger.error(f"Error initializing CVQueryApp: {str(e)}")
            raise
    
    def query(self, question: str) -> str:
        try:
            docs = self.vector_store.as_retriever(
                search_type="mmr",
                search_kwargs={"k": 8, "fetch_k": 20, "lambda_mult": 0.7}
            ).get_relevant_documents(question)
    
            context = "\n".join(f"[{doc.metadata['section']}]\n{doc.page_content}" for doc in docs)
    
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": (
                        "You are a precise CV analysis assistant. Your task is to:\n"
                        "1. Only use information explicitly stated in the provided CV sections\n"
                        "2. Quote specific details when possible\n"
                        "3. If information is not found, clearly state 'Information not found in CV'\n"
                        "4. Maintain chronological accuracy when discussing experience\n"
                        "5. Consider all provided sections before answering\n"
                        "6. Use relevant links of demos, where applicable, to emphasize skills"
                    )},
                    {"role": "user", "content": f"Based on these CV sections:\n{context}\n\nQuestion: {question}"}
                ],
                temperature=0.1,
                max_tokens=2000
            )
            return response.choices[0].message.content
    
        except Exception as e:
            return f"Error: {str(e)}"

app = dash.Dash(__name__)
server = app.server

app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>Stephen's Professional CV Assistant</title>
        {%favicon%}
        {%css%}
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
        <style>
            :root {
                --primary-color: #2563eb;
                --secondary-color: #3b82f6;
                --bg-color: #f8fafc;
                --user-bubble: #3b82f6;
                --bot-bubble: #e2e8f0;
            }
            
            * {
                font-family: 'Inter', sans-serif;
                box-sizing: border-box;
            }
            
            body {
                background-color: var(--bg-color);
                margin: 0;
                padding: 20px;
            }
            
            .chat-container {
                max-width: 800px;
                margin: 20px auto;
                background: white;
                border-radius: 16px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
                overflow: hidden;
            }
            
            .header {
                background: var(--primary-color);
                padding: 1.5rem;
                color: white;
                border-radius: 16px 16px 0 0;
            }
            
            .chat-history {
                padding: 1.5rem;
                height: 60vh;
                overflow-y: auto;
                background: linear-gradient(to bottom right, #f8fafc, #f1f5f9);
            }
            
            .message-container {
                display: flex;
                gap: 12px;
                margin-bottom: 1.5rem;
            }
            
            .user-message-container {
                flex-direction: row-reverse;
            }
            
            .message-bubble {
                max-width: 70%;
                padding: 1rem 1.25rem;
                border-radius: 1rem;
                position: relative;
                line-height: 1.5;
                font-size: 0.875rem;
            }
            
            .user-bubble {
                background: var(--user-bubble);
                color: white;
                border-radius: 1rem 1rem 0 1rem;
            }
            
            .bot-bubble {
                background: var(--bot-bubble);
                color: #1e293b;
                border-radius: 1rem 1rem 1rem 0;
            }
            
            .input-container {
                display: flex;
                gap: 12px;
                padding: 1.5rem;
                background: white;
                border-top: 1px solid #e2e8f0;
            }
            
            .question-chip {
                display: inline-block;
                padding: 8px 16px;
                background: #e2e8f0;
                border-radius: 8px;
                margin: 4px;
                cursor: pointer;
                transition: all 0.2s;
                font-size: 0.875rem;
            }
            
            .question-chip:hover {
                background: var(--secondary-color);
                color: white;
                transform: translateY(-2px);
            }
            
            .input-field {
                flex: 1;
                padding: 12px;
                border: 2px solid #e2e8f0;
                border-radius: 8px;
                font-size: 1rem;
                transition: border-color 0.2s;
            }
            
            .input-field:focus {
                outline: none;
                border-color: var(--primary-color);
            }
            
            .submit-button {
                background: var(--primary-color);
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 8px;
                cursor: pointer;
                transition: all 0.2s;
                font-weight: 500;
            }
            
            .submit-button:hover {
                background: var(--secondary-color);
                transform: translateY(-1px);
            }
            
            .typing-indicator {
                display: inline-flex;
                gap: 4px;
                padding: 8px 16px;
                background: var(--bot-bubble);
                border-radius: 8px;
            }
            
            .dot {
                width: 6px;
                height: 6px;
                background: #64748b;
                border-radius: 50%;
                animation: typing 1.4s infinite;
            }
            
            @keyframes typing {
                0%, 100% { transform: translateY(0); }
                50% { transform: translateY(-4px); }
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>{%config%}{%scripts%}{%renderer%}</footer>
    </body>
</html>
'''

app.layout = html.Div([
    html.Div([
        html.Div([
            html.H1("Stephen's Professional Profile Assistant", className='header'),
            html.Div([
                html.P("AI-powered CV analysis for hiring managers", style={'marginBottom': '0.5rem'}),
                html.Small("Ask about experience, technical skills, projects, or book recommendations"),
            ], style={'padding': '0 1.5rem', 'marginTop': '1rem'}),
            
            html.Div(id='chat-history', className='chat-history'),
            
            html.Div([
                dcc.Input(
                    id='user-input',
                    type='text',
                    placeholder='Ask about experience, skills, or projects...',
                    className='input-field',
                    autoComplete='off'
                ),
                html.Button('Send →', id='submit-button', className='submit-button'),
            ], className='input-container'),
            
            html.Div([
                html.Div("Example Questions:", style={'fontWeight': '500', 'marginBottom': '0.5rem'}),
                html.Div([
                    html.Span("Current role and company?", className='question-chip'),
                    html.Span("Technical skills?", className='question-chip'),
                    html.Span("Recent projects?", className='question-chip'),
                    html.Span("Book recommendations?", className='question-chip'),
                ], style={'marginBottom': '1rem'}),
            ], style={'padding': '0 1.5rem', 'marginBottom': '1.5rem'}),
            
            dcc.Store(id='chat-store', data=[]),
            dcc.Interval(id='fake-typing', interval=1000, disabled=True),
        ], className='chat-container')
    ])
])

@app.callback(
    [Output('chat-history', 'children'), 
     Output('chat-store', 'data'),
     Output('user-input', 'value'),
     Output('fake-typing', 'disabled')],
    [Input('submit-button', 'n_clicks'),
     Input('fake-typing', 'n_intervals')],
    [State('user-input', 'value'),
     State('chat-store', 'data')],
    prevent_initial_call=True
)
def update_chat(n_clicks, n_intervals, user_input, chat_history):
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update
    
    if 'submit-button' in ctx.triggered[0]['prop_id']:
        response = cv_app.query(user_input)
        chat_history.append({'user': user_input, 'bot': response})
        
        chat_messages = []
        for chat in chat_history:
            chat_messages.extend([
                html.Div([
                    html.Div(chat['user'], className='message-bubble user-bubble'),
                ], className='message-container user-message-container'),
                html.Div([
                    html.Div(chat['bot'], className='message-bubble bot-bubble'),
                ], className='message-container'),
            ])
        
        # Add temporary typing indicator
        chat_messages.append(
            html.Div([
                html.Div([
                    html.Div(className='dot'),
                    html.Div(className='dot'),
                    html.Div(className='dot'),
                ], className='typing-indicator')
            ], className='message-container')
        )
        
        return chat_messages, chat_history, '', False
    
    elif 'fake-typing' in ctx.triggered[0]['prop_id']:
        return dash.no_update, dash.no_update, dash.no_update, True
    
    return dash.no_update, dash.no_update, dash.no_update, True

if __name__ == '__main__':
    port = int(os.getenv("PORT", 7860))
    app.run_server(host='0.0.0.0', port=port, debug=True)

