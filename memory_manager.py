import os
import time
import chromadb
import ollama

class MemoryManager:
    def __init__(self, db_path="./memory_db", llm_model="qwen2.5:1.5b", num_thread=4, summarization_threshold=15):
        self.db_path = db_path
        self.llm_model = llm_model
        self.num_thread = num_thread
        self.summarization_threshold = summarization_threshold
        
        # Initialize ChromaDB persistent client
        self.client = chromadb.PersistentClient(path=self.db_path)
        
        # Get or create the memory collection
        self.collection = self.client.get_or_create_collection(
            name="assistant_memory"
        )
        
        # Keywords to filter out of long-term memory ingestion
        self.ignore_keywords = {
            "hey assistant", "computer", "jarvis", "system wake", 
            "ok", "okay", "yes", "no", "stop", "cancel", "exit", "quit"
        }

    def _should_ignore(self, text):
        """Determines if a user command should be excluded from memory to avoid noise."""
        if not text:
            return True
        
        clean_text = text.strip().lower()
        
        # Skip if extremely short (less than 3 words)
        if len(clean_text.split()) < 3:
            return True
            
        # Skip if it is purely a single wake/control phrase
        if clean_text in self.ignore_keywords:
            return True
            
        return False

    def add_interaction(self, user_input, assistant_response):
        """Adds a raw interaction to memory. Triggers Strategy C compaction if threshold met."""
        if self._should_ignore(user_input) or not assistant_response:
            return
            
        interaction_text = f"User: {user_input.strip()} | Assistant: {assistant_response.strip()}"
        doc_id = f"raw_{int(time.time() * 1000)}"
        
        # Add to ChromaDB
        self.collection.add(
            documents=[interaction_text],
            metadatas=[{"type": "raw", "timestamp": time.time()}],
            ids=[doc_id]
        )
        print(f"[MemoryManager] Logged raw interaction to Vector DB.")
        
        # Check raw count to trigger compaction
        raw_count = self.get_raw_count()
        if raw_count >= self.summarization_threshold:
            print(f"[MemoryManager] Raw memory count ({raw_count}) hit threshold. Consolidating...")
            try:
                self.consolidate_memories()
            except Exception as e:
                print(f"[MemoryManager Error] Failed to consolidate memories: {e}")

    def get_raw_count(self):
        """Returns the number of raw interactions currently stored."""
        results = self.collection.get(where={"type": "raw"})
        return len(results["ids"]) if results and "ids" in results else 0

    def detect_and_save_correction(self, user_input, last_user, last_ai):
        """Analyzes user input for corrections/mistakes and saves them to vector space."""
        if not user_input or not last_user or not last_ai:
            return
        
        # Fast keyword check to avoid running an LLM query on every turn
        correction_keywords = ["wrong", "mistake", "incorrect", "actually", "no, ", "false", "not true", "incorrectly", "that's wrong"]
        text_lower = user_input.lower()
        if not any(kw in text_lower for kw in correction_keywords):
            return
            
        print("[MemoryManager] Detected potential correction keywords. Analyzing...")
        
        prompt = (
            "You are a strict data extraction tool. Your job is to extract the correct fact from the user's feedback.\n\n"
            "Examples:\n"
            "- AI: 'You live in Giza.' User: 'Actually, I live in Cairo.' -> 'The user lives in Cairo.'\n"
            "- AI: 'You like black coffee.' User: 'No, that is a mistake, I hate coffee.' -> 'The user hates coffee.'\n"
            "- AI: 'Your screen shows code.' User: 'No, it shows a cat.' -> 'The user's screen shows a cat.'\n\n"
            f"AI's Statement: '{last_ai}'\n"
            f"User's Feedback: '{user_input}'\n\n"
            "Identify the corrected fact and output ONLY the single fact. If it is not a correction, output 'None'."
        )
        
        try:
            response = ollama.chat(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": "You are a factual correction extraction utility. Respond with 'None' or the extracted fact rule directly."},
                    {"role": "user", "content": prompt}
                ],
                options={"num_thread": self.num_thread, "temperature": 0.0}
            )
            
            correction_text = response["message"]["content"].strip()
            
            # Clean up the output in case it wrapped in quotes, etc.
            if correction_text.lower().startswith("none") or len(correction_text) < 4:
                print("[MemoryManager] No correction extracted by LLM.")
                return
                
            print(f"[MemoryManager] Successfully extracted correction: '{correction_text}'")
            
            doc_id = f"correction_{int(time.time() * 1000)}"
            self.collection.add(
                documents=[correction_text],
                metadatas=[{"type": "correction", "timestamp": time.time()}],
                ids=[doc_id]
            )
            print("[MemoryManager] Saved user correction override to database.")
            
        except Exception as e:
            print(f"[MemoryManager Warning] Failed to analyze correction: {e}")

    def retrieve_context(self, user_query, limit=3):
        """Retrieves matching raw memories, the consolidated user profile, and user corrections."""
        context_parts = []
        
        # 1. Fetch user corrections (mandatory rules)
        # We always want to fetch these because they are critical overrides!
        total_items = self.collection.count()
        if total_items > 0:
            try:
                corrections_results = self.collection.get(
                    where={"type": "correction"}
                )
                if corrections_results and corrections_results["documents"]:
                    docs = corrections_results["documents"]
                    rules = "\n".join([f"- {doc}" for doc in docs])
                    context_parts.append(f"### MANDATORY USER CORRECTIONS & RULES:\n{rules}")
            except Exception as e:
                print(f"[MemoryManager Warning] Could not retrieve corrections: {e}")
                
        # 2. Fetch the consolidated user profile (if exists)
        consolidated = self.collection.get(where={"type": "consolidated"})
        if consolidated and consolidated["documents"]:
            context_parts.append(f"### Established User Profile & Preferences:\n{consolidated['documents'][0]}")
            
        # 3. Fetch semantically similar raw memories matching user_query
        if total_items > 0:
            try:
                results = self.collection.query(
                    query_texts=[user_query],
                    n_results=limit,
                    where={"type": "raw"}
                )
                if results and results["documents"] and results["documents"][0]:
                    relevant_raw = results["documents"][0]
                    raw_context = "\n".join([f"- {doc}" for doc in relevant_raw])
                    context_parts.append(f"### Relevant Recent Interactions:\n{raw_context}")
            except Exception as e:
                print(f"[MemoryManager Warning] Could not search vector space: {e}")
                
        if not context_parts:
            return ""
            
        return "\n\n".join(context_parts)

    def consolidate_memories(self):
        """Runs Strategy C memory compaction via Ollama. Summarizes recent events and updates profile."""
        # 1. Fetch all raw interactions
        raw_results = self.collection.get(where={"type": "raw"})
        if not raw_results or not raw_results["documents"]:
            return
            
        raw_docs = raw_results["documents"]
        raw_ids = raw_results["ids"]
        
        # 2. Fetch existing consolidated profile
        cons_results = self.collection.get(where={"type": "consolidated"})
        existing_profile = ""
        cons_id = "user_consolidated_profile"
        
        if cons_results and cons_results["documents"]:
            existing_profile = cons_results["documents"][0]
            cons_id = cons_results["ids"][0]
            
        recent_interactions = "\n".join([f"- {doc}" for doc in raw_docs])
        
        # 3. Ask Ollama to consolidate them
        system_instruction = (
            "You are a memory consolidation assistant. Your task is to analyze the user's recent interactions with the AI assistant and update their profile.\n"
            "Combine the recent interactions with their existing profile, resolving any updates or new preferences.\n"
            "Keep the output as a concise, bulleted list of facts, preferences, habits, or details about the user (e.g. '- Prefers dark mode interface', '- Loves eating apples').\n"
            "Do NOT include conversational filler, greetings, or meta-commentary. Output ONLY the consolidated list of facts."
        )
        
        user_prompt = f"Existing Profile:\n{existing_profile or 'None'}\n\nRecent Interactions:\n{recent_interactions}"
        
        print("[MemoryManager] Sending memory chunk to Ollama for consolidation...")
        response = ollama.chat(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt}
            ],
            options={"num_thread": self.num_thread}
        )
        
        consolidated_profile = response["message"]["content"].strip()
        
        if consolidated_profile:
            # 4. Save the new consolidated profile
            # If it already exists, overwrite it. Note: Chroma's upsert can be used, or delete then add.
            if existing_profile:
                self.collection.delete(ids=[cons_id])
                
            self.collection.add(
                documents=[consolidated_profile],
                metadatas=[{"type": "consolidated", "timestamp": time.time()}],
                ids=[cons_id]
            )
            print("[MemoryManager] Successfully updated consolidated user profile.")
            
            # 5. Delete raw entries that were just consolidated to prevent duplicate entries
            self.collection.delete(ids=raw_ids)
            print(f"[MemoryManager] Purged {len(raw_ids)} raw interaction records from database.")
        else:
            print("[MemoryManager Warning] Consolidation returned empty response. Keeping raw logs.")

if __name__ == "__main__":
    print("--- Testing MemoryManager and ChromaDB Connection ---")
    
    # Instantiate memory manager in test database path
    test_db = "./test_memory_db"
    
    # Cleanup previous run
    import shutil
    if os.path.exists(test_db):
        shutil.rmtree(test_db)
        
    mm = MemoryManager(db_path=test_db, llm_model="qwen2.5:1.5b", summarization_threshold=3)
    
    print("\n1. Testing raw interaction additions...")
    mm.add_interaction("Remember that I love eating apples in the morning.", "I will remember that you like eating apples in the morning.")
    mm.add_interaction("I live in Cairo, Egypt.", "Got it. You live in Cairo, Egypt.")
    
    print(f"Current raw count (should be 2): {mm.get_raw_count()}")
    
    print("\n2. Testing context retrieval...")
    context = mm.retrieve_context("What do I like to eat?")
    print(f"Retrieved Context:\n{context}")
    
    print("\n3. Testing compaction trigger (adding 3rd raw interaction)...")
    # This should trigger consolidation since threshold is set to 3
    mm.add_interaction("I prefer programming in Python over Javascript.", "Understood. Python is your preferred language.")
    
    print(f"Raw count after consolidation (should be 0): {mm.get_raw_count()}")
    
    print("\n4. Checking if consolidated profile exists...")
    context_after = mm.retrieve_context("Where do I live?")
    print(f"Retrieved Context After Consolidation:\n{context_after}")
    
    print("\n5. Testing user correction extraction...")
    # Trigger correction analysis
    mm.detect_and_save_correction(
        user_input="No, that is wrong, actually I live in Cairo, not Alexandria.",
        last_user="Where do I live?",
        last_ai="Based on my records, you live in Alexandria."
    )
    
    print("\n6. Checking retrieved context with correction rule...")
    context_with_correction = mm.retrieve_context("Where do I live?")
    print(f"Retrieved Context With Correction:\n{context_with_correction}")
    
    # Clean up test database directory
    if os.path.exists(test_db):
        shutil.rmtree(test_db)
    print("\n--- MemoryManager Tests Completed ---")
