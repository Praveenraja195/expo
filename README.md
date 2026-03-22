# GENESIS: Student Academic Predictor & CRM

A full-stack Academic Insight Engine that uses Machine Learning to cluster students into specialized "Tribes" and provides an automated CRM for faculty communication via n8n.

## 🚀 Quick Start (Using Docker)

This project is fully containerized using Docker. Follow these steps to get it running on any machine:

### 1. Prerequisites
Ensure you have **Docker Desktop** installed on your machine.
- [Download Docker Desktop](https://www.docker.com/products/docker-desktop/)

### 2. Configuration (`.env`)
1. Create a copy of `.env.example` and rename it to `.env`.
2. Fill in your API keys:
   - **Groq API Keys**: For Llama 3 analysis.
   - **Gemini API Keys**: For fallback analysis and student chat.
   - **n8n Webhook URL**: The production URL for your email automation.

### 3. Launching the System
Open your terminal (Command Prompt or PowerShell) in the project folder and run:
```bash
docker compose up --build
```
Once the containers are running, open your browser to:
👉 **[http://localhost:5000](http://localhost:5000)**

## 🛠 Features
- **AI-Powered Analytics**: Uses K-Means clustering to predict student career trajectories.
- **n8n Email CRM**: Automated email dispatcher for HODs to contact students directly.
- **Interactive Radar Charts**: Real-time visualization of skill centroids.
- **Glassmorphism UI**: Beautiful, premium dark-mode dashboard.

## 👥 Sharing with Your Team
To share this with your teammates, just send them the entire project folder (excluding any personal `.env` files). They can follow the "Quick Start" guide above to be up and running in minutes.
