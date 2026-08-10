import express from 'express';
import axios from 'axios';
import * as dotenv from 'dotenv';

dotenv.config();

const app = express();
app.use(express.json());

const BASE_URL = process.env.CREDO_BASE_URL!;
const API_KEY  = process.env.CREDO_API_KEY!;
const TENANT   = process.env.CREDO_TENANT!;

async function getToken(): Promise<string> {
    const { data } = await axios.post(`${BASE_URL}/auth/token`, null, {
        headers: { 'X-API-Key': API_KEY, 'X-Tenant': TENANT },
    });
    return data.access_token;
}

async function createUseCase(name: string, description: string) {
    const token = await getToken();
    const { data } = await axios.post(
        `${BASE_URL}/use_cases`,
        { name, description },
        { headers: { Authorization: `Bearer ${token}`, 'X-Tenant': TENANT } }
    );
    return data;
}

app.post('/webhooks/jira', async (req, res) => {
    const payload = req.body;

    if (payload.webhookEvent !== 'jira:issue_created') {
        return res.json({ message: 'ignored' });
    }

    const { key, fields } = payload.issue;
    const name        = `[${key}] ${fields.summary}`;
    const description = fields.description ?? '';

    const useCase = await createUseCase(name, description);
    res.json({ use_case_id: useCase.id });
});

app.listen(5000, () => console.log('Listening on :5000'));
