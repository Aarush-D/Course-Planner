import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class BackendService {
  constructor(private http: HttpClient) {}

  async askPlanner(prompt: string, completed: string[] = []) {
    return firstValueFrom(
      this.http.post<any>('/api/plan', {
        prompt,
        completed
      })
    );
  }
}