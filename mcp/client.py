import os
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typing import List
import asyncio
import nest_asyncio

nest_asyncio.apply()
load_dotenv()

class MCP_ChatBot:

    def __init__(self):
        # Initialize session and client objects
        self.session: ClientSession = None
        # Initialize Gemini Client
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model_id = "gemini-2.0-flash" 
        self.available_tools_for_gemini = []

    async def process_query(self, query):
        # Initial message
        messages = [genai_types.Content(role="user", parts=[genai_types.Part(text=query)])]
        
        while True:
            # Generate content with tools
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=messages,
                config=genai_types.GenerateContentConfig(
                    tools=self.available_tools_for_gemini
                )
            )
            
            # Add assistant response to messages
            messages.append(response.candidates[0].content)
            
            # Check for tool calls
            tool_calls = [part.function_call for part in response.candidates[0].content.parts if part.function_call]
            
            if not tool_calls:
                # No more tool calls, print the text response
                for part in response.candidates[0].content.parts:
                    if part.text:
                        print(part.text)
                break
                
            # Handle tool calls
            tool_responses = []
            for tool_call in tool_calls:
                tool_name = tool_call.name
                tool_args = tool_call.args
                print(f"Calling tool {tool_name} with args {tool_args}")
                
                # MCP call
                result = await self.session.call_tool(tool_name, arguments=tool_args)
                
                # Extract text or JSON from MCP result content
                # MCP results are usually a list of content objects (text, image, etc.)
                result_text = ""
                for content_item in result.content:
                    if content_item.type == 'text':
                        result_text += content_item.text
                    # Note: Gemini Expects JSON or string for function response
                
                tool_responses.append(
                    genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=tool_name,
                            response={"result": result_text}
                        )
                    )
                )
            
            # Add tool responses to messages
            messages.append(genai_types.Content(role="user", parts=tool_responses))

    async def chat_loop(self):
        """Run an interactive chat loop"""
        print("\nMCP Gemini Chatbot Started!")
        print("Type your queries or 'quit' to exit.")
        
        while True:
            try:
                query = input("\nQuery: ").strip()
        
                if query.lower() == 'quit':
                    break
                
                if not query:
                    continue
                    
                await self.process_query(query)
                print("\n")
                    
            except Exception as e:
                print(f"\nError: {str(e)}")
    
    async def connect_to_server_and_run(self):
        # Use absolute paths for stability
        python_path = "/Users/dhruvsabharwal/Documents/personal/financial_analysis/venv/bin/python"
        server_path = "/Users/dhruvsabharwal/Documents/personal/financial_analysis/mcp/research_server.py"

        # Create server parameters for stdio connection
        server_params = StdioServerParameters(
            command=python_path,
            args=[server_path],
            env=None,
        )
        
        print(f"Connecting to MCP server at {server_path}...")
        
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                self.session = session
                # Initialize the connection
                await session.initialize()
    
                # List available tools
                response = await session.list_tools()
                tools = response.tools
                print("\nConnected to server with tools:", [tool.name for tool in tools])
                
                # Map MCP tools to Gemini function declarations
                function_declarations = []
                for tool in tools:
                    # tool.inputSchema is already a JSON Schema dictionary
                    # google-genai expects specific format or can handle dict if structure is right
                    decl = {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema
                    }
                    function_declarations.append(decl)
                
                self.available_tools_for_gemini = [
                    genai_types.Tool(function_declarations=function_declarations)
                ]
    
                await self.chat_loop()

async def main():
    chatbot = MCP_ChatBot()
    await chatbot.connect_to_server_and_run()

if __name__ == "__main__":
    asyncio.run(main())