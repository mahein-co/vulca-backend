import os
from django.conf import settings
from django.db import models
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.documents import Document as LangchainDocument
from pgvector.django import CosineDistance
from chatbot.models import DocumentPage, AccountingIndex
from chatbot.prompts import SYSTEM_PROMPT
import numpy as np

class DjangoVectorStore:
    """Wrapper pour utiliser Langchain avec nos modèles Django existants."""
    
    def __init__(self, project_id, date_start=None, date_end=None):
        self.project_id = project_id
        self.date_start = date_start
        self.date_end = date_end
        self.embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    def as_retriever(self, search_kwargs=None):
        # On définit une méthode qui mime le retriever de Langchain
        class DjangoRetriever:
            def __init__(self, parent):
                self.parent = parent
            
            def invoke(self, query):
                # 1. Générer l'embedding de la question
                print(f"[DEBUG] RAG: Generating embedding for query: {query}")
                try:
                    embedding = self.parent.embeddings.embed_query(query)
                    print(f"[DEBUG] RAG: Embedding generated successfully (size: {len(embedding)})")
                except Exception as e:
                    print(f"[ERROR] RAG: Embedding generation failed: {str(e)}")
                    raise e
                
                # 2. Recherche sur DocumentPage (Documents PDF)
                # Logique d'overlap : le document doit couvrir au moins une partie de la période demandée
                qs_docs = DocumentPage.objects.select_related("document").filter(
                    document__project_id=self.parent.project_id
                )
                
                if self.parent.date_start and self.parent.date_end:
                    qs_docs = qs_docs.filter(
                        models.Q(document__date_start__lte=self.parent.date_end, document__date_end__gte=self.parent.date_start) |
                        models.Q(document__date_start__isnull=True) |
                        models.Q(document__date_end__isnull=True)
                    )
                
                print(f"[DEBUG] RAG: Searching DocumentPage...")
                results_docs = qs_docs.annotate(
                    distance=CosineDistance("embedding", embedding)
                ).filter(distance__lt=0.9).order_by("distance")[:5]
                print(f"[DEBUG] RAG: Found {len(results_docs)} pages in PDF documents")
                
                # 3. Recherche sur AccountingIndex (Données Relationnelles)
                qs_acc = AccountingIndex.objects.filter(
                    project_id=self.parent.project_id
                )
                if self.parent.date_start:
                    qs_acc = qs_acc.filter(date__gte=self.parent.date_start)
                if self.parent.date_end:
                    qs_acc = qs_acc.filter(date__lte=self.parent.date_end)
                
                print(f"[DEBUG] RAG: Searching AccountingIndex...")
                results_acc = qs_acc.annotate(
                    distance=CosineDistance("embedding", embedding)
                ).filter(distance__lt=0.9).order_by("distance")[:5]
                print(f"[DEBUG] RAG: Found {len(results_acc)} entries in AccountingIndex")
                
                # 4. Fusion et conversion en documents Langchain
                all_results = []
                for res in results_docs:
                    all_results.append({
                        "content": res.content,
                        "distance": res.distance,
                        "metadata": {
                            "source": f"Document: {res.document.title}",
                            "type": "PDF",
                            "page": res.page_number
                        }
                    })
                
                for res in results_acc:
                    all_results.append({
                        "content": res.content,
                        "distance": res.distance,
                        "metadata": {
                            "source": f"Compta: {res.source_model} #{res.source_id}",
                            "type": "Relationnel",
                            "model": res.source_model
                        }
                    })
                
                # Tri par distance (plus petit = plus proche)
                all_results.sort(key=lambda x: x["distance"])
                
                # Retourner les top 7 résultats fusionnés
                docs = [
                    LangchainDocument(page_content=r["content"], metadata=r["metadata"])
                    for r in all_results[:7]
                ]
                return docs
        
        return DjangoRetriever(self)

class LangchainRAGService:
    def __init__(self, project_id, date_start=None, date_end=None):
        self.llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0.3)
        self.vectorstore = DjangoVectorStore(project_id, date_start, date_end)
        self.retriever = self.vectorstore.as_retriever()

    def get_response(self, user_input, history_messages, accounting_context=""):
        # 1. Reformulation de la question (History Aware)
        contextualize_q_system_prompt = (
            "Étant donné un historique de discussion et la dernière question de l'utilisateur, "
            "reformule la question pour qu'elle soit compréhensible de manière autonome, "
            "en résolvant les références contextuelles (ex: 'ce chiffre', 'la même période', 'ce compte', etc.). "
            "Ne réponds PAS à la question, reformule-la uniquement ou renvoie-la telle quelle si elle est déjà claire."
        )
        contextualize_q_prompt = ChatPromptTemplate.from_messages([
            ("system", contextualize_q_system_prompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ])
        
        # Chaîne de contextualisation
        contextualize_chain = contextualize_q_prompt | self.llm | StrOutputParser()

        # 2. Prompt final basé sur le SYSTEM_PROMPT unifié depuis prompts.py
        system_prompt_text = SYSTEM_PROMPT

        system_prompt_text += (
            "\n\nCONTEXTE DES DOCUMENTS :\n"
            "{context}\n\n"
        )
        
        if accounting_context:
            system_prompt_text += (
                "DONNÉES COMPTABLES CALCULÉES (SOURCE DE VÉRITÉ ABSOLUE) :\n"
                "{accounting_context}\n\n"
                "IMPORTANT : En cas de comparaison entre années ou périodes, utilise EXCLUSIVEMENT "
                "les chiffres de ce bloc. Calcule la différence et le pourcentage d'évolution si l'utilisateur le demande.\n\n"
            )

        qa_prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt_text),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ])

        # 3. Exécution via LCEL
        def format_docs(docs):
            return "\n\n".join(doc.page_content for doc in docs)

        # On récupère d'abord la question contextualisée
        if history_messages:
            contextualized_input = contextualize_chain.invoke({
                "input": user_input,
                "chat_history": history_messages
            })
        else:
            contextualized_input = user_input

        # On récupère les documents
        retrieved_docs = self.retriever.invoke(contextualized_input)
        context_text = format_docs(retrieved_docs)

        # Chaîne finale
        rag_chain = qa_prompt | self.llm | StrOutputParser()

        # Appel final
        ai_response = rag_chain.invoke({
            "context": context_text,
            "input": user_input,
            "chat_history": history_messages,
            "accounting_context": accounting_context
        })
        
        return ai_response, retrieved_docs
