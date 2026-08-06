import os
import random

def findPeakElement(lst):
    n = len(lst)
    if lst[0]>lst[1]:
        return 0
    if lst[-1]>lst[-2]:
        return n-1
    for i in range(1, n-1):
        if lst[i]>lst[i-1] and lst[i]>lst[i+1]:
            return i

def os_operate():
    if os.path.exists('my_project'):
        for path in os.listdir('my_project'):
            os.remove(os.path.join('my_project', path))
        os.rmdir('my_project')
    os.makedirs('my_project/docs')
    os.makedirs('my_project/src', exist_ok=True)
    with open('my_project/docs/README.txt', 'w') as f:
        f.write(random.randint(1, 10))
    f.close()
    with open('my_project/src/README.txt', 'w') as f:
        f.write(random.randint(1, 10))
    f.close()
